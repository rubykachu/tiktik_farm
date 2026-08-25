"""Scheduler-side driver for channel discovery scans.

Called synchronously from the main scheduler loop (scheduler/worker.py), not
from the pipeline thread pool, so a scan never overlaps a previous one and
never adds its own yt-dlp traffic on top of an in-flight scan. It DOES share
the same outbound IP as the pipeline downloads, so frequency and keyword
count are capped by discovery.scans_per_day / discovery.keywords_per_scan
(see api.services.discovery_settings) rather than firing on every poll.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import DiscoveryKeyword, DiscoveryScanRun
from api.services import channel_discovery as cd
from api.services import discovery_settings as settings

log = logging.getLogger("scheduler.discovery_worker")

# Hard operational ceilings, independent of the discovery.scans_per_day /
# discovery.keywords_per_scan settings: discovery shares the pipeline's
# outbound IP, so a misconfigured setting (scans_per_day=1000) must not be
# able to turn into near-continuous scanning and get that IP rate-limited —
# which would break the actual revenue-generating upload pipeline, not just
# discovery. Settings can only make scans LESS frequent / smaller than this,
# never more.
_MIN_SCAN_INTERVAL_SECONDS = 4 * 3600  # never more than 6 scans/day
_MAX_KEYWORDS_PER_SCAN = 5

# A scan updates channels_done after every channel (a few seconds apart at
# most); no progress for this long means the worker process died mid-scan.
_SCAN_STALE_AFTER = timedelta(minutes=10)


def _ensure_aware(dt: datetime) -> datetime:
    """Make a naive datetime UTC-aware (SQLite loses tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _last_scan_started_at():
    with Session(_api_db.engine) as s:
        row = s.exec(
            select(DiscoveryScanRun).order_by(DiscoveryScanRun.started_at.desc())
        ).first()
        return _ensure_aware(row.started_at) if row else None


def _is_due() -> bool:
    last = _last_scan_started_at()
    if last is None:
        return True
    interval_seconds = max(86_400 / max(settings.get_scans_per_day(), 1), _MIN_SCAN_INTERVAL_SECONDS)
    return (now_utc() - last).total_seconds() >= interval_seconds


def _pick_keywords() -> list[DiscoveryKeyword]:
    """Oldest last_scanned_at first — SQLite orders NULL (never scanned)
    before any real value in ASC order, so brand-new keywords are always
    scanned before the rotation reaches back to older ones."""
    limit = min(max(settings.get_keywords_per_scan(), 1), _MAX_KEYWORDS_PER_SCAN)
    with Session(_api_db.engine) as s:
        rows = s.exec(
            select(DiscoveryKeyword).order_by(DiscoveryKeyword.last_scanned_at).limit(limit)
        ).all()
        for r in rows:
            s.expunge(r)
        return rows


def reap_orphan_scan_runs() -> int:
    """Called on scheduler startup. A 'running' row with no progress update
    in _SCAN_STALE_AFTER means the worker died mid-scan — finalize it as
    errored so the webapp doesn't show 'scanning' forever (mirrors
    scheduler.pipeline_worker.reap_orphans for PipelineRun).

    Filtered in Python, not SQL — SQLite stores naive datetimes, and
    comparing them against an aware cutoff at the SQL layer is exactly the
    kind of TZ-drift bug api.db's module docstring calls out.
    """
    cutoff = now_utc() - _SCAN_STALE_AFTER
    count = 0
    with Session(_api_db.engine) as s:
        running = s.exec(select(DiscoveryScanRun).where(DiscoveryScanRun.status == "running")).all()
        for run in running:
            if _ensure_aware(run.updated_at) >= cutoff:
                continue
            run.status = "error"
            run.error_text = "worker crashed"
            run.finished_at = now_utc()
            s.add(run)
            count += 1
        if count:
            s.commit()
    return count


def _start_run(keywords: list[str]) -> int:
    with Session(_api_db.engine) as s:
        run = DiscoveryScanRun(keywords_json=json.dumps(keywords), status="running")
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id  # type: ignore[return-value]


def _update_progress(run_id: int, done: int, total: int) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(DiscoveryScanRun, run_id)
        if run is None:
            return
        run.channels_total = total
        run.channels_done = done
        run.updated_at = now_utc()
        s.add(run)
        s.commit()


def _finalize_ok(run_id: int, summary: cd.ScanSummary) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(DiscoveryScanRun, run_id)
        if run is None:
            return
        run.finished_at = now_utc()
        run.status = "done"
        run.channels_total = summary.channels_total
        run.channels_done = summary.channels_scanned
        run.candidates_found = summary.candidates_found
        s.add(run)
        s.commit()


def _finalize_error(run_id: int, error_text: str) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(DiscoveryScanRun, run_id)
        if run is None:
            return
        run.finished_at = now_utc()
        run.status = "error"
        run.error_text = error_text[:2000]
        s.add(run)
        s.commit()


def _mark_scanned(keyword_ids: list[int]) -> None:
    with Session(_api_db.engine) as s:
        for kid in keyword_ids:
            row = s.get(DiscoveryKeyword, kid)
            if row:
                row.last_scanned_at = now_utc()
                s.add(row)
        s.commit()


def discovery_tick() -> bool:
    """One scheduler poll's worth of discovery work. Returns True if a scan
    ran (finished or errored), False if it wasn't due yet or no keyword has
    been approved — a scan never runs on a keyword the user hasn't ticked.

    A pending "Quét ngay" request (cd.is_run_now_requested) bypasses the
    schedule interval AND the hard floor between scans — both exist to stop
    an automatic loop from over-scanning, not to make the user wait after an
    explicit one-off click. keywords_per_scan's cap still applies either way.
    """
    run_now = cd.is_run_now_requested()
    if not run_now and not _is_due():
        return False

    keyword_rows = _pick_keywords()
    if not keyword_rows:
        if run_now:
            cd.clear_run_now_requested()
        return False
    if run_now:
        cd.clear_run_now_requested()

    keywords = [k.keyword for k in keyword_rows]
    run_id = _start_run(keywords)
    log.info("discovery scan starting: keywords=%s", keywords)

    try:
        summary = cd.run_scan(
            keywords, progress_cb=lambda done, total: _update_progress(run_id, done, total)
        )
    except Exception as e:
        log.exception("discovery scan failed")
        _finalize_error(run_id, str(e))
        return True

    _finalize_ok(run_id, summary)
    _mark_scanned([k.id for k in keyword_rows])
    log.info(
        "discovery scan done: %d/%d channels scanned, %d candidates found, %d rejected",
        summary.channels_scanned, summary.channels_total,
        summary.candidates_found, summary.candidates_rejected,
    )
    return True
