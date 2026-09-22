"""Pipeline worker — recurring channel pipeline execution.

Handles: orphan reaper, claim, LockHeartbeat, run_pipeline, advance/clear,
and Telegram summary.
"""
from __future__ import annotations

import html
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import psutil
from sqlalchemy import delete as sa_delete, update
from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import (
    Account,
    ChannelPipeline,
    DouyinCookie,
    PipelineRun,
    PipelineSeenVideo,
    VideoUpload,
)
from api.services import (
    account_fields,
    douyin_channel,
    manual_download,
    pipeline_sources,
    telegram,
    tiktok_adapter,
)
from api.services import secrets
from api.services.llm_rewrite import rewrite_with_llm as _rewrite_with_llm
from api.services.caption import render_caption, template_uses_ai_title
from api.services.llm_config import (
    get_default_llm_model,
    get_llm_mode,
    get_ollama_config,
    is_llm_enabled,
)
from api.utils.text import truncate

log = logging.getLogger("scheduler.pipeline_worker")

LOCK_TTL = timedelta(minutes=10)
HEARTBEAT_INTERVAL = 300  # 5 min
ORPHAN_STALE_AFTER = timedelta(minutes=15)
MAX_UPLOAD_ATTEMPTS = 3
SUMMARY_MAX = 4096
# Shown in the Telegram link block. Kept in sync with the store's retention.
_LINK_RETENTION_HOURS = int(manual_download.RETENTION.total_seconds() // 3600)
def _parse_max_pipeline_seconds() -> int:
    raw = os.getenv("MAX_PIPELINE_SECONDS", "1800")
    try:
        val = int(raw)
    except ValueError:
        log.warning("MAX_PIPELINE_SECONDS=%r is not an integer, using 1800", raw)
        return 1800
    if val <= 0:
        log.warning("MAX_PIPELINE_SECONDS=%d must be positive, using 1800", val)
        return 1800
    return val

MAX_PIPELINE_SECONDS = _parse_max_pipeline_seconds()


@dataclass
class RunVideo:
    """One downloaded file, kept together with where it came from.

    Paired at collection time: the upload path and the download-only path fill
    these in at different points, so matching a file to its URL by list index
    afterwards would silently mismatch.
    """

    path: str
    source_url: str
    title: str
    # Rendered from the pipeline's template. On the download-only path this is
    # the whole point: the operator pastes it into TikTok by hand.
    caption: str = ""


@dataclass
class RunArtifacts:
    """Files and URLs the outer run context still needs after run_pipeline ends.

    Populated only when the pipeline attaches videos to Telegram or has no
    TikTok account. Deleting `cleanup_paths` is deferred to the outer context
    because the Telegram notification attaches files that live inside them.
    """

    cleanup_paths: list[str] = field(default_factory=list)
    videos: list[RunVideo] = field(default_factory=list)


def _seen_source(pipeline_id: int, source_key: str) -> bool:
    with Session(_api_db.engine) as s:
        return s.exec(
            select(PipelineSeenVideo).where(
                PipelineSeenVideo.pipeline_id == pipeline_id,
                PipelineSeenVideo.source_key == source_key,
            )
        ).first() is not None


def _mark_source_seen(pipeline_id: int, source_key: str) -> None:
    with Session(_api_db.engine) as s:
        s.add(PipelineSeenVideo(pipeline_id=pipeline_id, source_key=source_key))
        try:
            s.commit()
        except Exception:
            # Unique index hit — a concurrent tick already recorded it.
            s.rollback()


# ─── E1: Orphan reaper ─────────────────────────────────────────────────────


def reap_orphans() -> int:
    """Called on startup. Finalize stale PipelineRuns and clear expired locks."""
    count = 0
    now = now_utc()
    cutoff = now - ORPHAN_STALE_AFTER

    with Session(_api_db.engine) as s:
        stale_runs = s.exec(
            select(PipelineRun).where(
                PipelineRun.finished_at.is_(None),  # type: ignore[union-attr]
                PipelineRun.started_at < cutoff,
            )
        ).all()

        reaped_pipeline_ids: list[int] = []
        for run in stale_runs:
            run.finished_at = now
            run.error_text = "worker crashed"
            run.failed_count = run.pulled_count
            s.add(run)
            reaped_pipeline_ids.append(run.pipeline_id)
            count += 1

        if count:
            s.commit()

        # Clear expired locks
        result = s.exec(
            update(ChannelPipeline)
            .where(
                ChannelPipeline.lock_until.isnot(None),  # type: ignore[union-attr]
                ChannelPipeline.lock_until < now,  # type: ignore[operator]
            )
            .values(lock_until=None)
        )
        s.commit()
        count += result.rowcount  # type: ignore[operator]

    for pipeline_id in reaped_pipeline_ids:
        _notify_pipeline_alert(
            pipeline_id,
            "⚠️ Pipeline run reaped (stale/orphaned — likely a previous crash)",
        )

    return count


def _notify_pipeline_alert(pipeline_id: int, text: str) -> None:
    """Best-effort Telegram alert for a pipeline that never reported itself.

    Silent on a deleted pipeline or a Telegram failure — the caller is already
    on an error path and must not be derailed by the notification.
    """
    try:
        with Session(_api_db.engine) as s:
            pipeline = s.get(ChannelPipeline, pipeline_id)
        if pipeline is None:
            log.debug("Pipeline %d gone — skipping alert", pipeline_id)
            return
        if not pipeline.telegram_bot_token or not pipeline.telegram_chat_id:
            return
        telegram.send_message(
            pipeline.telegram_bot_token,
            pipeline.telegram_chat_id,
            f"📋 [{pipeline.name}]\n{text}",
        )
    except Exception as e:
        log.warning("Telegram alert failed for pipeline %d: %s", pipeline_id, e)


# ─── E1b: Retention purge ─────────────────────────────────────────────────


RUN_RETENTION_DAYS = 30


def purge_old_runs() -> int:
    """Delete pipeline_runs (+ their video_uploads) older than RUN_RETENTION_DAYS."""
    cutoff = now_utc() - timedelta(days=RUN_RETENTION_DAYS)
    # Subquery avoids loading IDs into Python (no SQLite variable-count limit).
    old_ids_q = select(PipelineRun.id).where(PipelineRun.started_at < cutoff)  # type: ignore[arg-type]
    with Session(_api_db.engine) as s:
        # Race guard: a VideoUpload just inserted by a concurrent tick (created_at≈now)
        # must not be deleted. NULL out its run_id first so the FK on PipelineRun
        # doesn't block the subsequent delete.
        s.exec(
            update(VideoUpload)
            .where(VideoUpload.run_id.in_(old_ids_q), VideoUpload.created_at >= cutoff)  # type: ignore[union-attr]
            .values(run_id=None)
        )
        s.exec(sa_delete(VideoUpload).where(  # type: ignore[union-attr]
            VideoUpload.run_id.in_(old_ids_q),
            VideoUpload.created_at < cutoff,
        ))
        deleted = s.exec(sa_delete(PipelineRun).where(PipelineRun.id.in_(old_ids_q))).rowcount
        s.commit()
    if deleted:
        log.info("purge_old_runs: deleted %d runs older than %d days", deleted, RUN_RETENTION_DAYS)
    return deleted


# ─── E2: Claim + LockHeartbeat ─────────────────────────────────────────────


def claim_due_pipeline() -> Optional[tuple[ChannelPipeline, str]]:
    """Atomically claim a pipeline whose next_fire_at is due or run_now flagged.

    Returns (pipeline, trigger_reason) or None.
    """
    now = now_utc()

    with Session(_api_db.engine) as s:
        row = s.exec(
            select(ChannelPipeline)
            .where(
                ChannelPipeline.state == "active",
                # lock must be free or expired
                (ChannelPipeline.lock_until.is_(None))  # type: ignore[union-attr]
                | (ChannelPipeline.lock_until < now),  # type: ignore[operator]
                # either scheduled or run-now
                (ChannelPipeline.next_fire_at <= now)
                | (ChannelPipeline.run_now_requested_at.isnot(None)),  # type: ignore[union-attr]
            )
            .order_by(
                ChannelPipeline.run_now_requested_at.is_(None),  # type: ignore[union-attr]
                ChannelPipeline.next_fire_at,
            )
            .limit(1)
        ).first()

        if not row:
            return None

        new_lock = now + LOCK_TTL
        result = s.exec(
            update(ChannelPipeline)
            .where(
                ChannelPipeline.id == row.id,
                ChannelPipeline.state == "active",
                (ChannelPipeline.lock_until.is_(None))  # type: ignore[union-attr]
                | (ChannelPipeline.lock_until < now),  # type: ignore[operator]
                (ChannelPipeline.next_fire_at <= now)
                | (ChannelPipeline.run_now_requested_at.isnot(None)),  # type: ignore[union-attr]
            )
            .values(lock_until=new_lock, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        s.commit()
        s.expire_all()

        if result.rowcount != 1:  # type: ignore[union-attr]
            return None

        pipeline = s.get(ChannelPipeline, row.id)
        if not pipeline:
            return None

        trigger = "manual" if pipeline.run_now_requested_at else "scheduled"
        # Detach from session for use outside
        s.expunge(pipeline)
        return pipeline, trigger


class LockHeartbeat:
    """Thread that bumps lock_until every HEARTBEAT_INTERVAL seconds."""

    def __init__(self, pipeline_id: int):
        self.pipeline_id = pipeline_id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_lock: Optional[datetime] = None

    def start(self, initial_lock: datetime) -> None:
        self._current_lock = initial_lock
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=HEARTBEAT_INTERVAL + 5)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            try:
                now = now_utc()
                new_lock = now + LOCK_TTL
                with Session(_api_db.engine) as s:
                    result = s.exec(
                        update(ChannelPipeline)
                        .where(
                            ChannelPipeline.id == self.pipeline_id,
                            ChannelPipeline.lock_until == self._current_lock,
                        )
                        .values(lock_until=new_lock)
                    )
                    s.commit()
                    if result.rowcount == 1:  # type: ignore[union-attr]
                        self._current_lock = new_lock
                    else:
                        log.warning("LockHeartbeat CAS failed for pipeline %d", self.pipeline_id)
                        return
            except Exception:
                log.exception("LockHeartbeat error for pipeline %d", self.pipeline_id)


# ─── E3: run_pipeline ──────────────────────────────────────────────────────


def _check_disk_space() -> bool:
    """Return False if disk free < 1GB."""
    path = os.environ.get("PIPELINE_TMP_DIR") or tempfile.gettempdir()
    os.makedirs(path, exist_ok=True)
    stat = os.statvfs(path)
    free_bytes = stat.f_bavail * stat.f_frsize
    return free_bytes >= 1_073_741_824


def _load_dedupe_set(session: Session, account_id: int, youtube_video_id: str) -> str:
    """Check dedupe: 'succeeded', 'exhausted', or 'upload'."""
    rows = session.exec(
        select(VideoUpload).where(
            VideoUpload.account_id == account_id,
            VideoUpload.youtube_video_id == youtube_video_id,
        )
    ).all()

    if any(r.status == "succeeded" for r in rows):
        return "skipped_dup"
    if sum(1 for r in rows if r.status == "failed") >= MAX_UPLOAD_ATTEMPTS:
        return "skipped_exhausted"
    return "upload"


def _has_future_upload_attempt(
    session: Session,
    *,
    videos: list[dict],
    account_ids: list[int],
    finished_video_idx: int,
    finished_account_idx: int,
) -> bool:
    """True if some remaining (video, account) would still call the uploader.

    Known limitation: repeats DB lookups (no cross-account dedupe cache); acceptable
    for small fetch_count / account lists.
    """
    cur_vid = videos[finished_video_idx]["id"]
    for j in range(finished_account_idx + 1, len(account_ids)):
        if _load_dedupe_set(session, account_ids[j], cur_vid) == "upload":
            return True
    for vj in range(finished_video_idx + 1, len(videos)):
        vid = videos[vj]["id"]
        for aid in account_ids:
            if _load_dedupe_set(session, aid, vid) == "upload":
                return True
    return False


def _maybe_upload_delay_after_record(
    pipeline: ChannelPipeline,
    session: Session,
    *,
    videos: list[dict],
    account_ids: list[int],
    finished_video_idx: int,
    finished_account_idx: int,
) -> None:
    delay_min = pipeline.upload_delay_minutes or 0
    if delay_min <= 0:
        return
    if not _has_future_upload_attempt(
        session,
        videos=videos,
        account_ids=account_ids,
        finished_video_idx=finished_video_idx,
        finished_account_idx=finished_account_idx,
    ):
        return
    log.info("Upload delay: sleeping %d minutes before next upload", delay_min)
    time.sleep(delay_min * 60)


def _ai_config_warn(pipeline: ChannelPipeline) -> Optional[str]:
    """Return a warning string if pipeline uses {ai_title} but LLM is misconfigured.

    Only checks static config — runtime failures (e.g. Ollama unreachable) are
    caught per-video inside _rewrite_with_llm and logged separately.
    """
    if not (pipeline.caption_ai_prompt and template_uses_ai_title(pipeline.caption_template)):
        return None
    if not is_llm_enabled():
        return "Tiêu đề được cấu hình nhưng tính năng AI đang tắt trong Cài đặt"
    if get_llm_mode() == "offline":
        model, base_url = get_ollama_config()
        if not model or not base_url:
            return "Tiêu đề được cấu hình nhưng OLLAMA_MODEL/OLLAMA_BASE_URL chưa được thiết lập"
    else:
        if get_default_llm_model() is None:
            return "Tiêu đề được cấu hình nhưng chưa có model AI mặc định trong Cài đặt"
    return None


def _load_douyin_cookie(pipeline: ChannelPipeline) -> Optional[dict]:
    """Return decrypted cookie dict or None if cookie row is missing."""
    if not pipeline.douyin_cookie_id:
        return None
    with Session(_api_db.engine) as s:
        row = s.get(DouyinCookie, pipeline.douyin_cookie_id)
        if not row:
            return None
    return json.loads(secrets.decrypt(row.cookie_json_encrypted))


def _load_douyin_dedupe(session: Session, account_id: int, aweme_id: str) -> str:
    rows = session.exec(
        select(VideoUpload).where(
            VideoUpload.account_id == account_id,
            VideoUpload.douyin_aweme_id == aweme_id,
        )
    ).all()
    if any(r.status == "succeeded" for r in rows):
        return "skipped_dup"
    if sum(1 for r in rows if r.status == "failed") >= MAX_UPLOAD_ATTEMPTS:
        return "skipped_exhausted"
    return "upload"


def _record_douyin_upload(
    *,
    run_id: int,
    pipeline_id: int,
    account_id: int,
    aweme_id: str,
    status: str,
    tiktok_video_id: Optional[str] = None,
    tiktok_url: Optional[str] = None,
    rendered_caption: Optional[str] = None,
    ai_title: Optional[str] = None,
    video_title: Optional[str] = None,
    attempt_count: int = 1,
    error_text: Optional[str] = None,
) -> None:
    row = VideoUpload(
        pipeline_id=pipeline_id,
        run_id=run_id,
        account_id=account_id,
        youtube_video_id="",
        youtube_url="",
        source_title=video_title,
        douyin_aweme_id=aweme_id,
        status=status,
        tiktok_video_id=tiktok_video_id,
        tiktok_url=tiktok_url,
        rendered_caption=rendered_caption,
        ai_title=ai_title,
        attempt_count=attempt_count,
        error_text=error_text,
    )
    with Session(_api_db.engine) as s:
        s.add(row)
        s.commit()


def _run_douyin_pipeline(
    pipeline: ChannelPipeline,
    trigger_reason: str,
    run_id: int,
    artifacts: Optional[RunArtifacts] = None,
) -> None:
    """Download from Douyin creator and upload each video per account.

    Mutates the PipelineRun row (via _finalize_run) on completion or error.
    """
    cookie_dict = _load_douyin_cookie(pipeline)
    if cookie_dict is None:
        _finalize_run(
            run_id,
            error_text=f"douyin_cookie_id={pipeline.douyin_cookie_id} not found — update cookie",
        )
        return

    base_tmp = os.environ.get("DOUYIN_TMP_DIR", os.path.join(tempfile.gettempdir(), "douyin_tmp"))
    os.makedirs(base_tmp, exist_ok=True)
    output_dir = tempfile.mkdtemp(prefix=f"{pipeline.id}_", dir=base_tmp)
    db_path = os.path.join(output_dir, "douyin_dl.db")

    _update_run_status(run_id, f"Downloading (up to {pipeline.fetch_count} videos)…")
    try:
        videos = douyin_channel.download_creator_posts(
            creator_url=pipeline.channel_url,
            fetch_count=pipeline.fetch_count,
            cookie_dict=cookie_dict,
            output_dir=output_dir,
            db_path=db_path,
            max_duration_seconds=pipeline.max_duration_seconds,
            sync_from_date=pipeline.sync_from_date,
        )
    except Exception as e:
        log.error("Douyin download failed for pipeline %d: %s", pipeline.id, e)
        _finalize_run(run_id, error_text=f"download failed: {e}")
        if artifacts is None:
            shutil.rmtree(output_dir, ignore_errors=True)
        else:
            artifacts.cleanup_paths.append(output_dir)
        return

    try:
        pulled_count = len(videos)
        uploaded_count = 0
        skipped_dup_count = 0
        skipped_exhausted_count = 0
        failed_count = 0
        upload_details: list[str] = []
        fail_details: list[str] = []

        account_ids: list[int] = json.loads(pipeline.account_ids_json)
        upload_opts: dict = json.loads(pipeline.upload_options_json)

        if not account_ids:
            fresh, skipped_dup_count = _split_seen(
                pipeline.id,  # type: ignore[arg-type]
                [
                    _SourceItem(
                        source_key=v.aweme_id,
                        source_url=douyin_channel.post_url(v.aweme_id),
                        title=v.title,
                        file_path=v.file_path,
                        description=v.description,
                    )
                    for v in videos
                ],
            )
            _finalize_download_only(
                pipeline, run_id, artifacts, fresh, skipped_dup_count
            )
            return

        use_ai_title = bool(
            pipeline.caption_ai_prompt
            and template_uses_ai_title(pipeline.caption_template)
        )
        ai_title_by_aweme: dict[str, str] = {}

        _update_run_status(run_id, f"Downloaded {pulled_count} videos, uploading…")

        for vi, video in enumerate(videos):
            aweme_id = video.aweme_id

            for account_id in account_ids:
                with Session(_api_db.engine) as s:
                    action = _load_douyin_dedupe(s, account_id, aweme_id)

                if action == "skipped_dup":
                    skipped_dup_count += 1
                    continue
                elif action == "skipped_exhausted":
                    skipped_exhausted_count += 1
                    continue

                with Session(_api_db.engine) as s:
                    acct = s.get(Account, account_id)
                    if not acct:
                        failed_count += 1
                        fail_details.append(f"- aweme {aweme_id} [account_id={account_id}]: missing")
                        continue
                    username = acct.username

                ai_title = video.title
                if use_ai_title:
                    if aweme_id in ai_title_by_aweme:
                        ai_title = ai_title_by_aweme[aweme_id]
                    else:
                        _update_run_status(run_id, f"Analyzing with LLM ({vi + 1}/{pulled_count})…")
                        rewritten = _rewrite_with_llm(
                            pipeline.caption_ai_prompt or "",
                            video.title,
                            video.description,
                            pipeline.channel_description or "",
                        )
                        if rewritten and rewritten != video.title:
                            ai_title_by_aweme[aweme_id] = rewritten
                            ai_title = rewritten

                _update_run_status(
                    run_id,
                    f"Uploading video {vi + 1}/{pulled_count} → @{username}…",
                )
                caption = render_caption(
                    pipeline.caption_template,
                    title=video.title,
                    ai_title=ai_title,
                )

                with Session(_api_db.engine) as s:
                    existing_failed = s.exec(
                        select(VideoUpload).where(
                            VideoUpload.account_id == account_id,
                            VideoUpload.douyin_aweme_id == aweme_id,
                            VideoUpload.status == "failed",
                        )
                    ).all()
                    attempt_count = len(existing_failed) + 1

                try:
                    ok, tiktok_id, tiktok_url = tiktok_adapter.upload_from_options(
                        username, video.file_path, caption, upload_opts
                    )
                except Exception as e:
                    ok, tiktok_id, tiktok_url = False, None, None
                    error_msg = str(e)[:500]
                    log.error("Upload exception for aweme %s@%s: %s", aweme_id, username, e)
                else:
                    error_msg = None if ok else "upload returned false"

                _record_douyin_upload(
                    run_id=run_id,
                    pipeline_id=pipeline.id,  # type: ignore[arg-type]
                    account_id=account_id,
                    aweme_id=aweme_id,
                    status="succeeded" if ok else "failed",
                    tiktok_video_id=tiktok_id,
                    tiktok_url=tiktok_url,
                    rendered_caption=caption,
                    ai_title=ai_title if use_ai_title and ai_title != video.title else None,
                    video_title=video.title,
                    attempt_count=attempt_count,
                    error_text=error_msg,
                )

                if ok:
                    uploaded_count += 1
                    upload_details.append(f"- aweme:{aweme_id} -> {tiktok_url or '?'} [{username}]")
                else:
                    failed_count += 1
                    fail_details.append(f"- aweme:{aweme_id} [{username}]: {truncate(error_msg or '', 120, suffix='')}")

            if artifacts is None:
                douyin_channel.cleanup_aweme_files(output_dir, aweme_id)
            else:
                artifacts.videos.append(RunVideo(
                    path=video.file_path,
                    source_url=douyin_channel.post_url(aweme_id),
                    title=video.title,
                ))

        summary_parts = [f"Pulled: {pulled_count}", f"Uploaded: {uploaded_count}"]
        if skipped_dup_count:
            summary_parts.append(f"Skipped (dup): {skipped_dup_count}")
        if skipped_exhausted_count:
            summary_parts.append(f"Skipped (exhausted): {skipped_exhausted_count}")
        if failed_count:
            summary_parts.append(f"Failed: {failed_count}")

        summary_text = "\n".join(summary_parts)
        if upload_details:
            summary_text += "\n\nUploaded:\n" + "\n".join(upload_details)
        if fail_details:
            summary_text += "\n\nFailed:\n" + "\n".join(fail_details)

        if pipeline.sync_from_date:
            summary_text = f"Cutoff: {pipeline.sync_from_date} | " + summary_text

        summary_text = truncate(summary_text, SUMMARY_MAX, suffix="\n…(truncated)")

        _finalize_run(
            run_id,
            pulled_count=pulled_count,
            uploaded_count=uploaded_count,
            skipped_dup_count=skipped_dup_count,
            skipped_exhausted_count=skipped_exhausted_count,
            failed_count=failed_count,
            summary_text=summary_text,
        )
    finally:
        if artifacts is not None:
            artifacts.cleanup_paths.append(output_dir)
        elif os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)


@dataclass
class _SourceItem:
    """One video from the source channel on the download-only path."""

    source_key: str  # YouTube video id or Douyin aweme id
    source_url: str
    title: str
    file_path: str = ""
    description: str = ""
    transcript: str = ""  # YouTube only — Douyin has no captions to draw from


# ---------- cancel a stuck YouTube download ---------------------------------
#
# Douyin is out of scope: its downloader is a vendored subprocess with a
# different process shape, not an ffmpeg mux we can target by temp dir.
#
# _active_downloads tracks, per pipeline, the temp_dir of whatever video is
# downloading right now — set right before the source module's download()
# and cleared right after, success or failure. check_cancel_requests() (called
# from the scheduler's poll loop, scheduler/worker.py) reads this to find the
# right ffmpeg process to kill when an admin requests a cancel.
_active_downloads: dict[int, str] = {}
_active_downloads_lock = threading.Lock()


def _register_active_download(pipeline_id: int, temp_dir: str) -> None:
    with _active_downloads_lock:
        _active_downloads[pipeline_id] = temp_dir


def _clear_active_download(pipeline_id: int) -> None:
    with _active_downloads_lock:
        _active_downloads.pop(pipeline_id, None)


def _kill_ffmpeg_under(temp_dir: str) -> int:
    """Kill any ffmpeg process whose command line references temp_dir.

    yt-dlp spawns ffmpeg as a subprocess to mux separately-downloaded video
    and audio streams; it gives us no handle to that process, so this matches
    on the one thing we do know — the output path we asked it to write into.
    Returns the number of processes killed.
    """
    killed = 0
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if proc.info["name"] != "ffmpeg":
                continue
            cmdline = proc.info["cmdline"] or []
            if not any(temp_dir in arg for arg in cmdline):
                continue
            proc.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def check_cancel_requests() -> None:
    """Act on any pending cancel-download request, then clear it.

    Called once per scheduler poll cycle (~30s) — a cancel click takes effect
    within one cycle, not instantly. A pipeline with no active download (the
    request arrived after the download already finished) just gets its flag
    cleared with nothing killed.
    """
    with Session(_api_db.engine) as s:
        pending = s.exec(
            select(ChannelPipeline).where(
                ChannelPipeline.cancel_requested_at.isnot(None)  # type: ignore[union-attr]
            )
        ).all()
        for row in pending:
            with _active_downloads_lock:
                temp_dir = _active_downloads.get(row.id)
            if temp_dir:
                killed = _kill_ffmpeg_under(temp_dir)
                log.info(
                    "cancel-download pipeline %d: killed %d ffmpeg process(es) under %s",
                    row.id, killed, temp_dir,
                )
            else:
                log.info(
                    "cancel-download pipeline %d: no active download found (already finished?)",
                    row.id,
                )
            row.cancel_requested_at = None
            s.add(row)
        if pending:
            s.commit()


def _split_seen(
    pipeline_id: int, items: list[_SourceItem]
) -> tuple[list[_SourceItem], int]:
    """Split source items into not-yet-reported and already-reported.

    Without a TikTok account there is no VideoUpload row to dedupe against, so
    PipelineSeenVideo is the only thing stopping a repeat notification.
    """
    fresh: list[_SourceItem] = []
    skipped = 0
    for item in items:
        if _seen_source(pipeline_id, item.source_key):
            skipped += 1
        else:
            fresh.append(item)
    return fresh, skipped


def _download_without_upload(
    pipeline: ChannelPipeline,
    run_id: int,
    videos: list[dict],
    artifacts: Optional[RunArtifacts],
) -> tuple[list[_SourceItem], int]:
    """Download every not-yet-seen video without uploading anywhere.

    Returns (successfully downloaded items, already-seen count). Download
    failures are logged and dropped so one bad video does not abort the tick.
    """
    source = pipeline_sources.get(pipeline.source_type)
    candidates = [
        _SourceItem(
            source_key=v["id"],
            source_url=v.get("url") or source.video_url(v["id"]),
            title=v.get("title", ""),
        )
        for v in videos
    ]
    fresh, skipped_dup_count = _split_seen(pipeline.id, candidates)  # type: ignore[arg-type]

    items: list[_SourceItem] = []
    for vi, item in enumerate(fresh):
        video_url = item.source_url
        _update_run_status(run_id, f"Downloading video {vi + 1}/{len(fresh)}…")
        temp_dir = tempfile.mkdtemp(
            prefix="pipeline_dl_",
            dir=os.environ.get("PIPELINE_TMP_DIR") or None,
        )
        if artifacts is not None:
            artifacts.cleanup_paths.append(temp_dir)
        _register_active_download(pipeline.id, temp_dir)  # type: ignore[arg-type]
        try:
            download_path, description, transcript = source.download(
                video_url, temp_dir, pipeline.max_duration_seconds
            )
        except source.VideoTooLong:
            log.info("Skipping %s — longer than max_duration_seconds", video_url)
            if artifacts is None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue
        except Exception as e:
            log.error("Download failed for %s: %s", video_url, e)
            if artifacts is None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue
        finally:
            _clear_active_download(pipeline.id)  # type: ignore[arg-type]
        item.file_path = download_path
        item.description = description
        item.transcript = transcript
        items.append(item)
    return items, skipped_dup_count


def _finalize_download_only(
    pipeline: ChannelPipeline,
    run_id: int,
    artifacts: Optional[RunArtifacts],
    fresh: list[_SourceItem],
    skipped_dup_count: int,
) -> None:
    """Finish a tick that has no TikTok account: nothing is uploaded, the
    downloaded files and their source URLs go to the Telegram notification.

    `fresh` is already filtered against PipelineSeenVideo by the caller.
    """
    use_ai_title = bool(
        pipeline.caption_ai_prompt and template_uses_ai_title(pipeline.caption_template)
    )

    for item in fresh:
        _mark_source_seen(pipeline.id, item.source_key)  # type: ignore[arg-type]
        if artifacts is None:
            continue
        if not os.path.exists(item.file_path):
            continue

        ai_title = item.title
        if use_ai_title:
            rewritten = _rewrite_with_llm(
                pipeline.caption_ai_prompt or "",
                item.title,
                item.description,
                pipeline.channel_description or "",
                transcript=item.transcript,
            )
            if rewritten:
                ai_title = rewritten

        artifacts.videos.append(RunVideo(
            path=item.file_path,
            source_url=item.source_url,
            title=item.title,
            caption=render_caption(
                pipeline.caption_template, title=item.title, ai_title=ai_title
            ),
        ))

    summary_parts = [f"Pulled: {len(fresh)}", "Uploaded: 0 (no TikTok account)"]
    if skipped_dup_count:
        summary_parts.append(f"Skipped (dup): {skipped_dup_count}")
    summary_text = "\n".join(summary_parts)
    if fresh:
        summary_text += "\n\nDownloaded:\n" + "\n".join(
            f"- {item.source_url}" for item in fresh
        )
    summary_text = truncate(summary_text, SUMMARY_MAX, suffix="\n…(truncated)")

    _finalize_run(
        run_id,
        pulled_count=len(fresh),
        uploaded_count=0,
        skipped_dup_count=skipped_dup_count,
        summary_text=summary_text,
    )


def run_pipeline(
    pipeline: ChannelPipeline,
    trigger_reason: str,
    artifacts: Optional[RunArtifacts] = None,
) -> PipelineRun:
    """Execute one pipeline tick. Returns the finalized PipelineRun.

    When `artifacts` is given, downloaded files are recorded there and their
    directories are NOT deleted here — the caller deletes them after the
    Telegram notification has had a chance to attach them.
    """
    now = now_utc()
    run = PipelineRun(
        pipeline_id=pipeline.id,  # type: ignore[arg-type]
        started_at=now,
        trigger_reason=trigger_reason,
    )

    with Session(_api_db.engine) as s:
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    ai_warn = _ai_config_warn(pipeline)
    if ai_warn:
        log.warning("Pipeline %d: %s", pipeline.id, ai_warn)

    # Disk check applies to both YouTube and Douyin (both write to /tmp)
    if not _check_disk_space():
        _finalize_run(run_id, error_text="low_disk")
        return _get_run(run_id)

    # Route Douyin pipelines to pass 3
    if pipeline.source_type == "douyin":
        _run_douyin_pipeline(pipeline, trigger_reason, run_id, artifacts)
        return _get_run(run_id)

    # List top-N videos
    source = pipeline_sources.get(pipeline.source_type)
    _update_run_status(run_id, f"Fetching video list (up to {pipeline.fetch_count})…")
    try:
        videos = source.list_top_n(
            pipeline.channel_url, pipeline.fetch_count,
            sync_from_date=pipeline.sync_from_date,
        )
    except Exception as e:
        log.error("list_top_n failed for pipeline %d: %s", pipeline.id, e)
        _finalize_run(run_id, error_text=f"list_top_n failed: {e}")
        return _get_run(run_id)

    pulled_count = len(videos)
    uploaded_count = 0
    skipped_dup_count = 0
    skipped_exhausted_count = 0
    failed_count = 0
    upload_details: list[str] = []
    fail_details: list[str] = []

    account_ids: list[int] = json.loads(pipeline.account_ids_json)
    upload_opts: dict = json.loads(pipeline.upload_options_json)

    if not account_ids:
        fresh, skipped_dup_count = _download_without_upload(
            pipeline, run_id, videos, artifacts
        )
        _finalize_download_only(
            pipeline, run_id, artifacts, fresh, skipped_dup_count
        )
        return _get_run(run_id)

    # LLM rewrite is opt-in per pipeline. Cache per video so multi-account
    # pipelines only pay for one rewrite per video.
    ai_title_by_video: dict[str, str] = {}
    use_ai_title = bool(
        pipeline.caption_ai_prompt
        and template_uses_ai_title(pipeline.caption_template)
    )

    for vi, video in enumerate(videos):
        video_id = video["id"]
        video_title = video.get("title", "")
        video_url = video.get("url") or source.video_url(video_id)

        # Lazy download — only when at least one account needs upload
        download_path: Optional[str] = None
        description: str = ""
        transcript: str = ""
        temp_dir: Optional[str] = None

        try:
            for ai, account_id in enumerate(account_ids):
                with Session(_api_db.engine) as s:
                    action = _load_dedupe_set(s, account_id, video_id)

                if action == "skipped_dup":
                    skipped_dup_count += 1
                    continue
                elif action == "skipped_exhausted":
                    skipped_exhausted_count += 1
                    continue

                if download_path is None:
                    temp_dir = tempfile.mkdtemp(
                        prefix="pipeline_dl_",
                        dir=os.environ.get("PIPELINE_TMP_DIR") or None,
                    )
                    _register_active_download(pipeline.id, temp_dir)  # type: ignore[arg-type]
                    try:
                        download_path, description, transcript = source.download(
                            video_url, temp_dir, pipeline.max_duration_seconds
                        )
                        if artifacts is not None:
                            artifacts.videos.append(RunVideo(
                                path=download_path,
                                source_url=video_url,
                                title=video_title,
                            ))
                    except source.VideoTooLong:
                        skipped_exhausted_count += 1
                        break  # skip all accounts for this video
                    except Exception as e:
                        log.error("Download failed for %s: %s", video_url, e)
                        # Record failed for each remaining account
                        for account_idx in range(ai, len(account_ids)):
                            aid = account_ids[account_idx]
                            _record_upload(
                                run_id=run_id,
                                pipeline_id=pipeline.id,  # type: ignore[arg-type]
                                account_id=aid,
                                video_id=video_id,
                                video_url=video_url,
                                video_title=video_title,
                                status="failed",
                                error_text=f"download failed: {e}",
                            )
                            failed_count += 1
                            fail_details.append(
                                f"- {video_url} [account_id={aid}]: download failed"
                            )
                        break
                    finally:
                        _clear_active_download(pipeline.id)  # type: ignore[arg-type]

                # Get username
                with Session(_api_db.engine) as s:
                    acct = s.get(Account, account_id)
                    if not acct:
                        failed_count += 1
                        fail_details.append(f"- {video_url} [account_id={account_id}]: account missing")
                        continue
                    username = acct.username

                # Resolve {ai_title} via LLM (cached per video). On any
                # failure, fall back to the raw YouTube title so the run
                # never aborts because of an LLM hiccup.
                ai_title = video_title
                if use_ai_title:
                    if video_id in ai_title_by_video:
                        ai_title = ai_title_by_video[video_id]
                    else:
                        _update_run_status(run_id, f"Analyzing with LLM ({vi + 1}/{pulled_count})…")
                        rewritten = _rewrite_with_llm(
                            pipeline.caption_ai_prompt or "",
                            video_title,
                            description,
                            pipeline.channel_description or "",
                            transcript=transcript,
                        )
                        # Only cache on success. A transient LLM failure must
                        # not poison the cache for the rest of the tick.
                        if rewritten and rewritten != video_title:
                            ai_title_by_video[video_id] = rewritten
                            ai_title = rewritten

                _update_run_status(
                    run_id,
                    f"Uploading video {vi + 1}/{pulled_count} → @{username}…",
                )
                # Render caption
                caption = render_caption(
                    pipeline.caption_template,
                    title=video_title,
                    ai_title=ai_title,
                )

                # Get existing failed count for attempt_count
                with Session(_api_db.engine) as s:
                    existing_failed = s.exec(
                        select(VideoUpload).where(
                            VideoUpload.account_id == account_id,
                            VideoUpload.youtube_video_id == video_id,
                            VideoUpload.status == "failed",
                        )
                    ).all()
                    attempt_count = len(existing_failed) + 1

                # Upload
                try:
                    ok, tiktok_id, tiktok_url = tiktok_adapter.upload_from_options(
                        username, download_path, caption, upload_opts
                    )
                except Exception as e:
                    ok, tiktok_id, tiktok_url = False, None, None
                    error_msg = str(e)[:500]
                    log.error("Upload exception for %s@%s: %s", video_id, username, e)
                else:
                    error_msg = None if ok else "upload returned false"

                _record_upload(
                    run_id=run_id,
                    pipeline_id=pipeline.id,  # type: ignore[arg-type]
                    account_id=account_id,
                    video_id=video_id,
                    video_url=video_url,
                    video_title=video_title,
                    status="succeeded" if ok else "failed",
                    tiktok_video_id=tiktok_id,
                    tiktok_url=tiktok_url,
                    rendered_caption=caption,
                    ai_title=ai_title if use_ai_title and ai_title != video_title else None,
                    attempt_count=attempt_count,
                    error_text=error_msg,
                )

                if ok:
                    uploaded_count += 1
                    upload_details.append(
                        f"- {video_url} -> {tiktok_url or '?'}  [account: {username}]"
                    )
                else:
                    failed_count += 1
                    err_short = truncate(error_msg or "", 120, suffix="")
                    fail_details.append(f"- {video_url} [account: {username}]: {err_short}")

                with Session(_api_db.engine) as s:
                    _maybe_upload_delay_after_record(
                        pipeline,
                        s,
                        videos=videos,
                        account_ids=account_ids,
                        finished_video_idx=vi,
                        finished_account_idx=ai,
                    )

        finally:
            if temp_dir:
                if artifacts is not None:
                    artifacts.cleanup_paths.append(temp_dir)
                elif os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

    # Build summary
    summary_parts = [
        f"Pulled: {pulled_count}",
        f"Uploaded: {uploaded_count}",
    ]
    if skipped_dup_count:
        summary_parts.append(f"Skipped (dup): {skipped_dup_count}")
    if skipped_exhausted_count:
        summary_parts.append(f"Skipped (exhausted): {skipped_exhausted_count}")
    if failed_count:
        summary_parts.append(f"Failed: {failed_count}")

    summary_text = "\n".join(summary_parts)
    if upload_details:
        summary_text += "\n\nUploaded:\n" + "\n".join(upload_details)
    if fail_details:
        summary_text += "\n\nFailed:\n" + "\n".join(fail_details)

    if pipeline.sync_from_date:
        summary_text = f"Cutoff: {pipeline.sync_from_date} | " + summary_text

    summary_text = truncate(summary_text, SUMMARY_MAX, suffix="\n…(truncated)")

    _finalize_run(
        run_id,
        pulled_count=pulled_count,
        uploaded_count=uploaded_count,
        skipped_dup_count=skipped_dup_count,
        skipped_exhausted_count=skipped_exhausted_count,
        failed_count=failed_count,
        summary_text=summary_text,
    )

    return _get_run(run_id)


def _record_upload(
    *,
    run_id: int,
    pipeline_id: int,
    account_id: int,
    video_id: str,
    video_url: str,
    video_title: str,
    status: str,
    tiktok_video_id: Optional[str] = None,
    tiktok_url: Optional[str] = None,
    rendered_caption: Optional[str] = None,
    ai_title: Optional[str] = None,
    attempt_count: int = 1,
    error_text: Optional[str] = None,
) -> None:
    row = VideoUpload(
        pipeline_id=pipeline_id,
        run_id=run_id,
        account_id=account_id,
        youtube_video_id=video_id,
        youtube_url=video_url,
        source_title=video_title,
        status=status,
        tiktok_video_id=tiktok_video_id,
        tiktok_url=tiktok_url,
        rendered_caption=rendered_caption,
        ai_title=ai_title,
        attempt_count=attempt_count,
        error_text=error_text,
    )
    with Session(_api_db.engine) as s:
        s.add(row)
        s.commit()


def _update_run_status(run_id: int, status_text: str) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(PipelineRun, run_id)
        if not run:
            return
        run.status_text = status_text
        s.add(run)
        s.commit()


def _finalize_run(run_id: int, **kwargs) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(PipelineRun, run_id)
        if not run:
            return
        run.finished_at = now_utc()
        run.status_text = None
        for k, v in kwargs.items():
            setattr(run, k, v)
        s.add(run)
        s.commit()


def _get_run(run_id: int) -> PipelineRun:
    with Session(_api_db.engine) as s:
        run = s.get(PipelineRun, run_id)
        if run:
            s.expunge(run)
        return run  # type: ignore[return-value]


# ─── E4: Advance next_fire_at + clear run_now CAS ──────────────────────────


def _ensure_aware(dt: datetime) -> datetime:
    """Make a naive datetime UTC-aware (SQLite loses tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _next_fire_in_window(pipeline: ChannelPipeline, prev: datetime) -> Optional[datetime]:
    """Return prev + window_delay if still within today's window, else None.

    "Today's window" is anchored to the calendar date of prev (UTC), so window
    boundaries don't drift across day changes.
    """
    if not pipeline.window_end_time or not pipeline.window_delay_minutes:
        return None
    candidate = prev + timedelta(minutes=pipeline.window_delay_minutes)
    end_h, end_m = map(int, pipeline.window_end_time.split(":"))
    # Window closes at window_end_time on the same UTC date as prev.
    window_close = prev.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if candidate <= window_close:
        return candidate
    return None


def advance_next_fire(
    pipeline: ChannelPipeline, trigger_reason: str, skip_window: bool = False
) -> bool:
    """Advance next_fire_at (scheduled) or clear run_now flag (manual).

    skip_window=True forces next-cycle scheduling even if still inside window
    (used when a successful upload means no need to retry within the window).

    Returns whether this tick closed the schedule cycle. A window fires many
    times while it waits for the author to post; only the tick that closes the
    window is worth an "found nothing" notification, and every tick of a
    non-window pipeline closes its own cycle.
    """
    now = now_utc()
    cycle_closed = True

    with Session(_api_db.engine) as s:
        fresh = s.get(ChannelPipeline, pipeline.id)
        if not fresh:
            return True

        if trigger_reason == "scheduled":
            prev = _ensure_aware(fresh.next_fire_at)
            in_window = None if skip_window else _next_fire_in_window(fresh, prev)
            if in_window is not None and in_window > now:
                fresh.next_fire_at = in_window
                cycle_closed = False
            else:
                interval = _interval_to_timedelta(fresh.interval_value, fresh.interval_unit)
                if fresh.window_end_time:
                    # Window closed → reopen next cycle at first_fire_at's
                    # time-of-day. Anchoring to first_fire_at (not prev, which
                    # is the last in-window fire) stops the daily window-start
                    # from drifting toward window_end_time.
                    ffa = _ensure_aware(fresh.first_fire_at)
                    nxt = prev.replace(
                        hour=ffa.hour, minute=ffa.minute, second=0, microsecond=0
                    ) + interval
                    while nxt <= now:  # skip missed cycles after downtime
                        nxt += interval
                    fresh.next_fire_at = nxt
                else:
                    candidate_a = prev + interval
                    candidate_b = now + interval
                    fresh.next_fire_at = max(candidate_a, candidate_b)
        elif trigger_reason == "manual":
            # CAS: only clear if value hasn't changed during tick
            claimed_at = pipeline.run_now_requested_at
            if claimed_at and fresh.run_now_requested_at == claimed_at:
                fresh.run_now_requested_at = None

        fresh.updated_at = now
        s.add(fresh)
        s.commit()

    return cycle_closed


def _interval_to_timedelta(value: int, unit: str) -> timedelta:
    if unit == "minutes":
        return timedelta(minutes=value)
    elif unit == "hours":
        return timedelta(hours=value)
    elif unit == "days":
        return timedelta(days=value)
    return timedelta(hours=value)


def release_lock(pipeline_id: int) -> None:
    """Clear lock_until after tick completes."""
    with Session(_api_db.engine) as s:
        result = s.exec(
            update(ChannelPipeline)
            .where(ChannelPipeline.id == pipeline_id)
            .values(lock_until=None, updated_at=now_utc())
        )
        s.commit()


# ─── E5: Telegram summary ──────────────────────────────────────────────────

# Messages use parse_mode=HTML so a 110-character signed URL can hide behind a
# few words, and so a caption can sit in <code>, which Telegram copies on tap.
MAX_VIDEO_BLOCKS = 5
TITLE_MAX = 60
CAPTION_MAX = 200
ERROR_MAX = 120

# Known failures, phrased for whoever reads the message rather than the log.
# Matched as a substring against the raw error, first hit wins.
_FRIENDLY_ERRORS: tuple[tuple[str, str], ...] = (
    ("HTTP Error 403", "nguồn chặn tải xuống — thử nâng yt-dlp"),
    ("low_disk", "máy chủ hết dung lượng đĩa"),
    ("Sign in to confirm", "nguồn yêu cầu đăng nhập"),
    ("Private video", "video đã chuyển sang riêng tư"),
    ("Video unavailable", "video không còn tồn tại"),
    ("KeyError: 'project'", "cookie TikTok hết hạn — nhập lại cookie"),
    ("status_code\":8", "cookie TikTok hết hạn — nhập lại cookie"),
    ("not found — update cookie", "cookie Douyin không còn — cập nhật lại"),
    ("worker crashed", "worker dừng đột ngột"),
    ("upload returned false", "TikTok từ chối video"),
)


def _esc(value: Optional[str]) -> str:
    """Escape for parse_mode=HTML. Titles and captions are attacker-adjacent."""
    return html.escape(value or "", quote=False)


def _friendly_error(raw: Optional[str]) -> str:
    if not raw:
        return ""
    for needle, message in _FRIENDLY_ERRORS:
        if needle in raw:
            return message
    return truncate(raw, ERROR_MAX, suffix="…")


def _local_time(dt: Optional[datetime], with_date: bool = True) -> str:
    from zoneinfo import ZoneInfo

    if dt is None:
        return ""
    local = dt.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    return local.strftime("%H:%M %d/%m") if with_date else local.strftime("%H:%M")


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


def _account_names(account_ids: list[int]) -> dict[int, str]:
    if not account_ids:
        return {}
    with Session(_api_db.engine) as s:
        rows = s.exec(select(Account).where(Account.id.in_(account_ids))).all()
    return {r.id: r.username for r in rows if r.id is not None}


def _attach_fields_block(pipeline: ChannelPipeline) -> list[str]:
    """Render the credentials block, or [] when there is nothing to render.

    Every account on the pipeline is listed — the operator picks which one to
    upload with by hand.
    """
    try:
        fields = json.loads(pipeline.telegram_attach_fields_json or "[]")
        account_ids = json.loads(pipeline.account_ids_json or "[]")
    except ValueError:
        return []
    if not fields or not account_ids:
        return []

    with Session(_api_db.engine) as s:
        accounts = [s.get(Account, aid) for aid in account_ids]

    lines: list[str] = []
    for acct in accounts:
        if acct is None:
            continue
        values = [account_fields.resolve(acct, f) or "—" for f in fields]
        lines.append(f"🔑 {_esc(acct.username)} — <code>{_esc(' / '.join(values))}</code>")
    return lines


def _publish_video_links(
    pipeline: ChannelPipeline, artifacts: RunArtifacts
) -> dict[str, str]:
    """Move each downloaded video into the retained store, keyed by source URL.

    Sidesteps Telegram's 50 MB upload cap: the message carries a signed URL
    instead of the file. The files then live under the same 24h retention as a
    manually pasted download.
    """
    links: dict[str, str] = {}
    for video in artifacts.videos:
        published = manual_download.publish(
            file_path=video.path,
            url=video.source_url,
            source_type=pipeline.source_type,
            title=video.title,
            pipeline_id=pipeline.id,
        )
        if published is None:
            continue
        # The file moved out of the run's temp dir. Follow it, or the attach
        # step below would upload nothing.
        video.path = published.file_path
        links[video.source_url] = published.url
    return links


def _send_video_attachments(pipeline: ChannelPipeline, video_paths: list[str]) -> None:
    """Attach downloaded videos to the notification, best effort.

    Telegram caps bot uploads at 50 MB, so anything larger degrades to the
    text-only message that was already sent (which carries the links).
    """
    existing = [p for p in video_paths if os.path.exists(p)]
    if not existing:
        return

    token = pipeline.telegram_bot_token
    chat_id = pipeline.telegram_chat_id
    total = sum(os.path.getsize(p) for p in existing)
    try:
        if total <= telegram.TELEGRAM_MAX_UPLOAD_BYTES:
            dropped = len(existing) - telegram.TELEGRAM_MEDIA_GROUP_MAX
            telegram.send_media_group(token, chat_id, existing)  # type: ignore[arg-type]
            if dropped > 0:
                log.info(
                    "Pipeline %d: %d video(s) over the sendMediaGroup limit were "
                    "not attached; their links are in the message",
                    pipeline.id, dropped,
                )
            return
        first = existing[0]
        if os.path.getsize(first) > telegram.TELEGRAM_MAX_UPLOAD_BYTES:
            log.info(
                "Pipeline %d: video %s exceeds the Telegram upload limit — "
                "sent links only", pipeline.id, first,
            )
            return
        telegram.send_document(token, chat_id, first)  # type: ignore[arg-type]
        log.info(
            "Pipeline %d: total %d bytes over the Telegram limit — attached the "
            "first video only", pipeline.id, total,
        )
    except Exception as e:
        log.warning("Telegram attachment failed for pipeline %d: %s", pipeline.id, e)


def _upload_blocks(
    run: PipelineRun, links: dict[str, str], want_caption: bool
) -> list[str]:
    """One numbered block per (video, account) attempt of this run."""
    if run.id is None:
        return []
    try:
        with Session(_api_db.engine) as s:
            uploads = list(
                s.exec(select(VideoUpload).where(VideoUpload.run_id == run.id)).all()
            )
    except Exception:
        log.warning("Failed to fetch VideoUpload for Telegram summary run %d", run.id)
        return []

    try:
        names = _account_names([u.account_id for u in uploads])
    except Exception:
        names = {}
    blocks: list[str] = []
    for i, u in enumerate(uploads[:MAX_VIDEO_BLOCKS], start=1):
        title = truncate(u.ai_title or u.source_title or "video", TITLE_MAX)
        who = _esc(names.get(u.account_id, f"account #{u.account_id}"))
        lines = [f"{i}. {_esc(title)}"]

        if u.status == "succeeded":
            posted = _link(u.tiktok_url, "Xem trên TikTok") if u.tiktok_url else "đã đăng"
            lines.append(f"   ✅ @{who} · {posted}")
        else:
            lines.append(f"   ❌ @{who} — {_esc(_friendly_error(u.error_text))}")
            # A failed upload is exactly when the operator wants to post by
            # hand, so hand them the caption and the file.
            if want_caption and u.rendered_caption:
                lines.append(
                    f"   📄 <code>{_esc(truncate(u.rendered_caption, CAPTION_MAX))}</code>"
                )
            link = links.get(u.youtube_url or "")
            if link:
                lines.append(f"   {_link(link, '⬇️ Tải video để đăng tay')}")

        blocks.append("\n".join(lines))

    remaining = len(uploads) - len(blocks)
    if remaining > 0:
        blocks.append(f"… và {remaining} video khác")
    return blocks


def _download_blocks(artifacts: RunArtifacts, links: dict[str, str]) -> list[str]:
    """One numbered block per downloaded video on the no-account path."""
    blocks: list[str] = []
    for i, video in enumerate(artifacts.videos[:MAX_VIDEO_BLOCKS], start=1):
        lines = [f"{i}. {_esc(truncate(video.title or 'video', TITLE_MAX))}"]
        if video.caption:
            lines.append(
                f"   📄 <code>{_esc(truncate(video.caption, CAPTION_MAX))}</code>"
            )
        parts = []
        link = links.get(video.source_url)
        if link:
            parts.append(_link(link, "⬇️ Tải video"))
        if video.source_url:
            parts.append(_link(video.source_url, "🔗 Nguồn"))
        if parts:
            lines.append("   " + " · ".join(parts))
        blocks.append("\n".join(lines))

    remaining = len(artifacts.videos) - len(blocks)
    if remaining > 0:
        blocks.append(f"… và {remaining} video khác")
    return blocks


def _headline(
    pipeline: ChannelPipeline, run: Optional[PipelineRun], has_accounts: bool
) -> tuple[str, str]:
    """(emoji, one-line verdict) — the part read from the notification preview."""
    if run is None:
        return "🛑", "Worker dừng đột ngột"
    if run.error_text:
        return "🛑", f"Không chạy được: {_esc(_friendly_error(run.error_text))}"

    if not has_accounts:
        if run.pulled_count:
            return "📥", f"Đã tải {run.pulled_count} video, chờ bạn đăng"
        return "😴", "Không có video mới"

    if run.uploaded_count and not run.failed_count:
        return "✅", f"Đã đăng {run.uploaded_count} video"
    if run.failed_count and not run.uploaded_count:
        return "❌", f"Không đăng được video nào · {run.failed_count} lỗi"
    if run.uploaded_count or run.failed_count:
        total = run.uploaded_count + run.failed_count
        bits = [f"Đăng được {run.uploaded_count}/{total}"]
        if run.failed_count:
            bits.append(f"{run.failed_count} lỗi")
        if run.skipped_dup_count:
            bits.append(f"{run.skipped_dup_count} trùng")
        return "⚠️", " · ".join(bits)
    return "😴", "Không có video mới"


def _next_fire_line(pipeline: ChannelPipeline) -> str:
    """Read next_fire_at back from the DB — advance_next_fire has already run.

    A DB hiccup here must not cost the whole notification; the schedule line is
    the least important part of it.
    """
    try:
        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            if fresh is None or fresh.state != "active":
                return ""
            return f"Lần chạy tới: {_local_time(_ensure_aware(fresh.next_fire_at))}"
    except Exception:
        log.warning("Could not read next_fire_at for pipeline %s", pipeline.id)
        return ""


def run_produced_something(pipeline: ChannelPipeline, run: Optional[PipelineRun]) -> bool:
    """Whether this tick has anything worth reporting on its own.

    Videos only skipped as duplicates do not count — that is the steady state
    of a pipeline polling a channel that has not posted yet.
    """
    if run is None:
        return True
    if run.error_text or run.failed_count:
        return True
    if run.uploaded_count:
        return True
    return not _has_accounts(pipeline) and run.pulled_count > 0


def should_notify(
    pipeline: ChannelPipeline, run: Optional[PipelineRun], cycle_closed: bool
) -> bool:
    """Whether a tick with nothing new still sends a message.

    A window fires many times while it waits for the author to post; notifying
    on each poll is noise, but never notifying makes a dead scheduler
    indistinguishable from a quiet channel. "cycle" is the middle: one message
    when the window closes, so a missing message is a real signal.
    """
    if run_produced_something(pipeline, run):
        return True
    mode = pipeline.telegram_notify_mode or "cycle"
    if mode == "every_tick":
        return True
    if mode == "cycle":
        return cycle_closed
    return False


def send_telegram_summary(
    pipeline: ChannelPipeline,
    run: PipelineRun,
    artifacts: Optional[RunArtifacts] = None,
    cycle_closed: bool = True,
) -> None:
    """Send the Telegram notification if the pipeline has bot config."""
    if not pipeline.telegram_bot_token or not pipeline.telegram_chat_id:
        return
    if not should_notify(pipeline, run, cycle_closed):
        log.info(
            "Pipeline %d: nothing new and notify mode is %s — no message sent",
            pipeline.id, pipeline.telegram_notify_mode,
        )
        return

    # Publishing moves the files out of the run's temp dirs, so it has to
    # happen before the message is built and before the outer context deletes
    # those dirs.
    links = _publish_video_links(pipeline, artifacts) if artifacts is not None else {}

    has_accounts = _has_accounts(pipeline)
    emoji, verdict = _headline(pipeline, run, has_accounts)
    started = _local_time(run.started_at or now_utc())

    parts = [f"{emoji} <b>{_esc(pipeline.name)}</b> · {started}", verdict]

    ai_warn = _ai_config_warn(pipeline)
    if ai_warn:
        parts[1] = f"{verdict} · {_esc(ai_warn)}"

    blocks = (
        _download_blocks(artifacts, links)
        if not has_accounts and artifacts is not None
        else _upload_blocks(run, links, want_caption=True)
    )
    if blocks:
        parts.append("")
        parts.append("\n".join(blocks))

    credentials = _attach_fields_block(pipeline)
    if credentials:
        parts.append("")
        parts.extend(credentials)

    footer = []
    if links:
        footer.append("⏳ Link hết hạn sau 24h")
    next_fire = _next_fire_line(pipeline)
    if next_fire:
        footer.append(next_fire)
    if footer:
        parts.append("")
        parts.append("\n".join(footer))

    text = "\n".join(parts)
    if len(text) > 4096:
        text = text[:4090] + "\n…"

    try:
        telegram.send_message(
            pipeline.telegram_bot_token, pipeline.telegram_chat_id, text, html=True
        )
        if run.id is not None:
            with Session(_api_db.engine) as s:
                db_run = s.get(PipelineRun, run.id)
                if db_run:
                    db_run.pushed_notify_at = now_utc()
                    s.add(db_run)
                    s.commit()
    except Exception as e:
        log.warning("Telegram notify failed for pipeline %d: %s", pipeline.id, e)

    if pipeline.telegram_attach_video and artifacts is not None:
        _send_video_attachments(pipeline, [v.path for v in artifacts.videos])


# ─── Pipeline run context manager ─────────────────────────────────────────


def _has_accounts(pipeline: ChannelPipeline) -> bool:
    try:
        return bool(json.loads(pipeline.account_ids_json or "[]"))
    except ValueError:
        return False


def _needs_artifacts(pipeline: ChannelPipeline) -> bool:
    """True when downloaded files must outlive run_pipeline.

    A pipeline wired to Telegram gets a download link per video, so its files
    move into the 24h store instead of being deleted at the end of the tick. A
    pipeline with no Telegram and at least one account keeps deleting each
    video right after its upload, so disk use there stays flat.
    """
    return bool(pipeline.telegram_bot_token and pipeline.telegram_chat_id) or not _has_accounts(
        pipeline
    )


@dataclass
class TickOutcome:
    """Filled in by the tick body, read by the notification in the finally.

    Defaults to a closed cycle so a crash — which never reaches the body —
    always notifies.
    """

    cycle_closed: bool = True


@contextmanager
def _pipeline_run_context(
    pipeline: ChannelPipeline,
    trigger_reason: str,
    outcome: Optional[TickOutcome] = None,
):
    """Run pipeline with timeout, always send Telegram summary in finally (even on crash)."""
    run: Optional[PipelineRun] = None
    artifacts = RunArtifacts() if _needs_artifacts(pipeline) else None
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(run_pipeline, pipeline, trigger_reason, artifacts)
        try:
            run = fut.result(timeout=MAX_PIPELINE_SECONDS)
        except FutureTimeoutError:
            log.error(
                "Pipeline %d exceeded %ds timeout — finalizing and moving on",
                pipeline.id, MAX_PIPELINE_SECONDS,
            )
            raise TimeoutError(f"pipeline {pipeline.id} exceeded {MAX_PIPELINE_SECONDS}s")
        finally:
            ex.shutdown(wait=False)
        yield run
    except Exception:
        log.exception("Unhandled error in pipeline_tick for pipeline %d", pipeline.id)
        _emergency_finalize(pipeline.id)  # type: ignore[arg-type]
        _notify_pipeline_alert(
            pipeline.id,  # type: ignore[arg-type]
            "⚠️ Pipeline crashed (worker timeout or unhandled exception)",
        )
        try:
            with Session(_api_db.engine) as s:
                run = s.exec(
                    select(PipelineRun)
                    .where(PipelineRun.pipeline_id == pipeline.id)
                    .order_by(PipelineRun.started_at.desc())
                ).first()
        except Exception:
            log.exception("Failed to fetch run after crash for pipeline %d", pipeline.id)
        raise
    finally:
        if run is not None:
            try:
                send_telegram_summary(
                    pipeline, run, artifacts,
                    cycle_closed=outcome.cycle_closed if outcome else True,
                )
            except Exception:
                # Deletion below must still run, so the notification failure
                # is logged here instead of escaping the finally block.
                log.exception("send_telegram_summary failed for pipeline %d", pipeline.id)
        # Deleted even when the run crashed (run is None) so a failed tick
        # never leaves downloaded videos behind.
        if artifacts is not None:
            for path in artifacts.cleanup_paths:
                if os.path.exists(path):
                    shutil.rmtree(path, ignore_errors=True)


# ─── Pipeline tick (composed) ──────────────────────────────────────────────


def pipeline_tick() -> bool:
    """One pipeline poll cycle. Returns True if a pipeline was processed."""
    claimed = claim_due_pipeline()
    if not claimed:
        return False

    pipeline, trigger_reason = claimed

    hb = LockHeartbeat(pipeline.id)  # type: ignore[arg-type]
    hb.start(pipeline.lock_until or now_utc() + LOCK_TTL)

    outcome = TickOutcome()
    try:
        with _pipeline_run_context(pipeline, trigger_reason, outcome) as run:
            # A window keeps polling until the author posts; landing a video
            # closes it early. A download-only pipeline never uploads, so its
            # signal is pulled_count instead of uploaded_count.
            produced = run is not None and run_produced_something(pipeline, run)
            outcome.cycle_closed = advance_next_fire(
                pipeline, trigger_reason, skip_window=produced
            )
    except Exception:
        advance_next_fire(pipeline, trigger_reason)
    finally:
        hb.stop()
        release_lock(pipeline.id)  # type: ignore[arg-type]

    return True


def _emergency_finalize(pipeline_id: int) -> None:
    """Finalize any in-flight PipelineRun for this pipeline so it isn't orphaned."""
    try:
        with Session(_api_db.engine) as s:
            s.exec(
                update(PipelineRun)
                .where(
                    PipelineRun.pipeline_id == pipeline_id,
                    PipelineRun.finished_at.is_(None),  # type: ignore[union-attr]
                    PipelineRun.error_text.is_(None),  # type: ignore[union-attr]
                )
                .values(finished_at=now_utc(), error_text="worker crashed")
                .execution_options(synchronize_session=False)
            )
            s.exec(
                update(PipelineRun)
                .where(
                    PipelineRun.pipeline_id == pipeline_id,
                    PipelineRun.finished_at.is_(None),  # type: ignore[union-attr]
                    PipelineRun.error_text.isnot(None),  # type: ignore[union-attr]
                )
                .values(finished_at=now_utc())
                .execution_options(synchronize_session=False)
            )
            s.commit()
    except Exception:
        log.exception("_emergency_finalize itself failed for pipeline %d", pipeline_id)
