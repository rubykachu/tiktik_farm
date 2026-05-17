"""Pipeline worker — pass 2 of the scheduler tick.

Handles: orphan reaper, claim, LockHeartbeat, run_pipeline, advance/clear,
and Telegram summary. Complements the single-shot pass in worker.py.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update
from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
from api.services import llm, tiktok_adapter, telegram, youtube_channel
from api.services.caption import render_caption, template_uses_ai_title
from api.services.llm_config import get_default_llm_model, is_llm_enabled
from api.utils.text import truncate

log = logging.getLogger("scheduler.pipeline_worker")

LOCK_TTL = timedelta(minutes=10)
HEARTBEAT_INTERVAL = 300  # 5 min
ORPHAN_STALE_AFTER = timedelta(minutes=15)
MAX_UPLOAD_ATTEMPTS = 3
SUMMARY_MAX = 4096


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
    stat = os.statvfs("/tmp")
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


def _rewrite_with_llm(prompt: str, title: str, description: str) -> str:
    """Return the rewritten title, or `title` on any failure path."""
    if not is_llm_enabled():
        log.info("LLM globally disabled; using raw title for {ai_title}")
        return title
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
        )
    except (llm.LLMNotConfigured, llm.LLMCallFailed) as e:
        log.warning("LLM rewrite skipped: %s", e)
        return title
    return rewritten or title


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

    # Disk check
    if not _check_disk_space():
        _finalize_run(run_id, error_text="low_disk")
        return _get_run(run_id)

    # List top-N videos
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
                        temp_dir = tempfile.mkdtemp(prefix="pipeline_dl_")
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
                        ai_title = _rewrite_with_llm(
                            pipeline.caption_ai_prompt or "",
                            video_title,
                            description,
                        )
                        ai_title_by_video[video_id] = ai_title

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


def _finalize_run(run_id: int, **kwargs) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(PipelineRun, run_id)
        if not run:
            return
        run.finished_at = now_utc()
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


def advance_next_fire(pipeline: ChannelPipeline, trigger_reason: str) -> None:
    """Advance next_fire_at (scheduled) or clear run_now flag (manual)."""
    now = now_utc()

    with Session(_api_db.engine) as s:
        fresh = s.get(ChannelPipeline, pipeline.id)
        if not fresh:
            return

        if trigger_reason == "scheduled":
            interval = _interval_to_timedelta(fresh.interval_value, fresh.interval_unit)
            prev = _ensure_aware(fresh.next_fire_at)
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
    started_str = run.started_at.astimezone(tz).strftime("%H:%M")

    lines = [f"[{pipeline.name}] — {started_str}", ""]
    lines.append(f"Pulled: {run.pulled_count}")
    lines.append(f"Uploaded: {run.uploaded_count}")
    if run.skipped_dup_count:
        lines.append(f"Skipped (dup): {run.skipped_dup_count}")
    if run.skipped_exhausted_count:
        lines.append(f"Skipped (exhausted): {run.skipped_exhausted_count}")
    if run.failed_count:
        lines.append(f"Failed: {run.failed_count}")

    if run.error_text:
        lines.append(f"\nError: {truncate(run.error_text, 200, suffix='')}")

    text = "\n".join(lines)

    try:
        telegram.send_message(pipeline.telegram_bot_token, pipeline.telegram_chat_id, text)
    except Exception as e:
        log.warning("Telegram notify failed for pipeline %d: %s", pipeline.id, e)


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
        run = run_pipeline(pipeline, trigger_reason)
        advance_next_fire(pipeline, trigger_reason)
        send_telegram_summary(pipeline, run)
    except Exception:
        log.exception("Unhandled error in pipeline_tick for pipeline %d", pipeline.id)
        _emergency_finalize(pipeline.id)  # type: ignore[arg-type]
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
                .values(finished_at=now_utc(), error_text="worker_crash")
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
