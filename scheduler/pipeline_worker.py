"""Pipeline worker — recurring channel pipeline execution.

Handles: orphan reaper, claim, LockHeartbeat, run_pipeline, advance/clear,
and Telegram summary.
"""
from __future__ import annotations

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
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete as sa_delete, update
from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, DouyinCookie, PipelineRun, VideoUpload
from api.services import douyin_channel, llm, tiktok_adapter, telegram, youtube_channel
from api.services import secrets
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

        for run in stale_runs:
            run.finished_at = now
            run.error_text = "worker crashed"
            run.failed_count = run.pulled_count
            s.add(run)
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

    return count


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


def _rewrite_with_llm(
    prompt: str, title: str, description: str, channel_description: str = ""
) -> str:
    """Return the rewritten title, or `title` on any failure path."""
    if not is_llm_enabled():
        log.warning("LLM globally disabled; using raw title: %s", title)
        return title

    if get_llm_mode() == "offline":
        model, base_url = get_ollama_config()
        if not model or not base_url:
            log.warning("OLLAMA_MODEL/OLLAMA_BASE_URL not set; using raw title")
            return title
        try:
            rewritten = llm.rewrite_title_offline(
                prompt,
                title=title,
                description=description,
                model=model,
                base_url=base_url,
                channel_description=channel_description,
            )
        except llm.LLMCallFailed as e:
            log.warning("Ollama rewrite skipped: %s", e)
            return title
        return rewritten or title

    default = get_default_llm_model()
    if default is None:
        log.warning("Pipeline has caption_ai_prompt but no default LLM is set")
        return title
    try:
        rewritten = llm.rewrite_title(
            prompt,
            title=title,
            description=description,
            model_id=default.model_id,
            api_key_encrypted=default.api_key_encrypted,
            api_base=default.api_base,
            api_version=default.api_version,
            channel_description=channel_description,
        )
    except (llm.LLMNotConfigured, llm.LLMCallFailed) as e:
        log.warning("LLM rewrite skipped: %s", e)
        return title
    return rewritten or title


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
    attempt_count: int = 1,
    error_text: Optional[str] = None,
) -> None:
    row = VideoUpload(
        pipeline_id=pipeline_id,
        run_id=run_id,
        account_id=account_id,
        youtube_video_id="",
        youtube_url="",
        douyin_aweme_id=aweme_id,
        status=status,
        tiktok_video_id=tiktok_video_id,
        tiktok_url=tiktok_url,
        rendered_caption=rendered_caption,
        attempt_count=attempt_count,
        error_text=error_text,
    )
    with Session(_api_db.engine) as s:
        s.add(row)
        s.commit()


def _run_douyin_pipeline(pipeline: ChannelPipeline, trigger_reason: str, run_id: int) -> None:
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
        )
    except Exception as e:
        log.error("Douyin download failed for pipeline %d: %s", pipeline.id, e)
        _finalize_run(run_id, error_text=f"download failed: {e}")
        shutil.rmtree(output_dir, ignore_errors=True)
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
                    attempt_count=attempt_count,
                    error_text=error_msg,
                )

                if ok:
                    uploaded_count += 1
                    upload_details.append(f"- aweme:{aweme_id} -> {tiktok_url or '?'} [{username}]")
                else:
                    failed_count += 1
                    fail_details.append(f"- aweme:{aweme_id} [{username}]: {truncate(error_msg or '', 120, suffix='')}")

            douyin_channel.cleanup_aweme_files(output_dir, aweme_id)

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
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)


def run_pipeline(pipeline: ChannelPipeline, trigger_reason: str) -> PipelineRun:
    """Execute one pipeline tick. Returns the finalized PipelineRun."""
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
        _run_douyin_pipeline(pipeline, trigger_reason, run_id)
        return _get_run(run_id)

    # List top-N videos
    _update_run_status(run_id, f"Fetching video list (up to {pipeline.fetch_count})…")
    try:
        videos = youtube_channel.list_top_n(pipeline.channel_url, pipeline.fetch_count)
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
        video_url = video.get("url", f"https://www.youtube.com/watch?v={video_id}")

        # Lazy download — only when at least one account needs upload
        download_path: Optional[str] = None
        description: str = ""
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
                    try:
                        temp_dir = tempfile.mkdtemp(
                            prefix="pipeline_dl_",
                            dir=os.environ.get("PIPELINE_TMP_DIR") or None,
                        )
                        download_path, description = youtube_channel.download(
                            video_url, temp_dir, pipeline.max_duration_seconds
                        )
                    except youtube_channel.VideoTooLong:
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
            if temp_dir and os.path.exists(temp_dir):
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
    attempt_count: int = 1,
    error_text: Optional[str] = None,
) -> None:
    row = VideoUpload(
        pipeline_id=pipeline_id,
        run_id=run_id,
        account_id=account_id,
        youtube_video_id=video_id,
        youtube_url=video_url,
        youtube_title=video_title,
        status=status,
        tiktok_video_id=tiktok_video_id,
        tiktok_url=tiktok_url,
        rendered_caption=rendered_caption,
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


def advance_next_fire(pipeline: ChannelPipeline, trigger_reason: str) -> None:
    """Advance next_fire_at (scheduled) or clear run_now flag (manual)."""
    now = now_utc()

    with Session(_api_db.engine) as s:
        fresh = s.get(ChannelPipeline, pipeline.id)
        if not fresh:
            return

        if trigger_reason == "scheduled":
            prev = _ensure_aware(fresh.next_fire_at)
            in_window = _next_fire_in_window(fresh, prev)
            if in_window is not None and in_window > now:
                fresh.next_fire_at = in_window
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


def send_telegram_summary(pipeline: ChannelPipeline, run: PipelineRun) -> None:
    """Send Telegram notification if pipeline has bot config."""
    if not pipeline.telegram_bot_token or not pipeline.telegram_chat_id:
        return

    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    started_str = (run.started_at or now_utc()).astimezone(tz).strftime("%H:%M")

    parts = [f"📋 [{pipeline.name}] — {started_str}", ""]

    ai_warn = _ai_config_warn(pipeline)
    if ai_warn:
        parts.append(f"⚠️ {ai_warn}")
        parts.append("")

    if run.error_text:
        parts.append(f"❌ Lỗi hệ thống: {truncate(run.error_text, 200)}")
    else:
        parts.append(f"⬇️ Tải xuống: {run.pulled_count}")
        parts.append(f"✅ Đăng tải: {run.uploaded_count}")
        if run.skipped_dup_count:
            parts.append(f"⏭️ Bỏ qua (trùng): {run.skipped_dup_count}")
        if run.skipped_exhausted_count:
            parts.append(f"⏭️ Bỏ qua (hết): {run.skipped_exhausted_count}")
        if run.failed_count:
            parts.append(f"⚠️ Thất bại: {run.failed_count}")

    uploads: list[VideoUpload] = []
    if run.id is not None:
        try:
            with Session(_api_db.engine) as s:
                uploads = list(
                    s.exec(select(VideoUpload).where(VideoUpload.run_id == run.id)).all()
                )
        except Exception:
            log.warning("Failed to fetch VideoUpload for Telegram summary run %d", run.id)

    if uploads:
        parts.extend(["", "📼 Chi tiết:"])
        header = "\n".join(parts)

        video_lines: list[str] = []
        for v in uploads:
            title = truncate(v.youtube_title or "video", 60)
            if v.status == "succeeded" and v.tiktok_url:
                video_lines.append(f"  ✅ {title}\n     {v.tiktok_url}")
            elif v.error_text:
                video_lines.append(f"  ❌ {title}\n     {truncate(v.error_text, 80)}")
            else:
                video_lines.append(f"  ⏭️ {title}")

        body: list[str] = []
        used = len(header)
        for i, vl in enumerate(video_lines):
            if used + len(vl) + 1 > 3500:
                body.append(f"\n... và {len(video_lines) - i} video khác")
                break
            body.append(vl)
            used += len(vl) + 1

        text = header + "\n" + "\n".join(body)
    else:
        text = "\n".join(parts)

    if len(text) > 4096:
        text = text[:4090] + "\n…"

    try:
        telegram.send_message(pipeline.telegram_bot_token, pipeline.telegram_chat_id, text)
    except Exception as e:
        log.warning("Telegram notify failed for pipeline %d: %s", pipeline.id, e)


# ─── Pipeline run context manager ─────────────────────────────────────────


@contextmanager
def _pipeline_run_context(pipeline: ChannelPipeline, trigger_reason: str):
    """Run pipeline with timeout, always send Telegram summary in finally (even on crash)."""
    run: Optional[PipelineRun] = None
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(run_pipeline, pipeline, trigger_reason)
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
            send_telegram_summary(pipeline, run)


# ─── Pipeline tick (composed) ──────────────────────────────────────────────


def pipeline_tick() -> bool:
    """One pipeline poll cycle. Returns True if a pipeline was processed."""
    claimed = claim_due_pipeline()
    if not claimed:
        return False

    pipeline, trigger_reason = claimed

    hb = LockHeartbeat(pipeline.id)  # type: ignore[arg-type]
    hb.start(pipeline.lock_until or now_utc() + LOCK_TTL)

    try:
        with _pipeline_run_context(pipeline, trigger_reason):
            advance_next_fire(pipeline, trigger_reason)
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
