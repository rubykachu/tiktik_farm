"""Channel discovery: keyword search + ranking, across YouTube, TikTok and
Douyin.

This is NOT an auto-picker: it rounds up candidate channels for a keyword
search and ranks them, and api.routers.discovery / the webapp let the user
add or skip each one by hand. There is no automatic scheduling — every scan
is triggered by the "Quét ngay" button with an explicit platform + keyword
set + market, and runs as an API background task (see
api.routers.discovery.scan_now).

Everything platform-specific — how a keyword becomes channels and how a
channel becomes numbers — lives in api.services.discovery_sources; its
base.py documents the interface and holds the scoring. This module owns
what is the same for every platform: deduping against channels the user
already has or already decided on, the silence cutoff, persistence,
progress and the final run status.

Every scoring threshold (min subscribers, silence cutoff, cadence target,
view reference scale) is read from api.services.discovery_settings, never
hardcoded. Platform, market and the keyword set are per-scan parameters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryScanResult, DiscoveryScanRun
from api.services import discovery_sources
from api.services import discovery_settings as settings
from api.services.discovery_sources.base import ChannelData, ChannelRef, score

log = logging.getLogger("api.services.channel_discovery")


# ---------- keyword set normalization -----------------------------------------


def normalize_keyword_set(keywords: list[str], platform: str) -> str:
    """Canonical key for one platform's batch of keywords, order/case/
    whitespace insensitive — re-scanning the same set on the same platform
    overwrites the same DiscoveryScanRun row (see run_scan) instead of
    creating a new history entry. The same keywords on another platform are
    a different scan and get their own row."""
    cleaned = sorted({kw.strip().lower() for kw in keywords if kw.strip()})
    if not cleaned:
        return ""
    return f"{platform}:" + "|".join(cleaned)


# ---------- dedupe against existing pipelines ---------------------------------


def normalize_channel_key(url: str) -> str:
    """Best-effort dedupe key against an existing ChannelPipeline.channel_url:
    the last non-empty path segment, lowercased. Only matches when both URLs
    use the same representation ('/channel/UCxxx' vs '/channel/UCxxx', or
    '/@handle' vs '/@handle') — a pipeline saved by @handle will not match a
    candidate found by channel_id. Resolving both to a canonical channel_id
    would need a yt-dlp call per existing pipeline on every scan, which costs
    more than the duplicate check is worth.
    """
    path = urlparse(url.strip()).path.rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams", "/featured", "/about"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return path.rsplit("/", 1)[-1].lower()


# ---------- orchestration --------------------------------------------------


@dataclass
class ScanSummary:
    channels_total: int
    channels_scanned: int
    candidates_found: int
    candidates_rejected: int


def suggest_keywords(platform: str, seed: str, gl: str, hl: str) -> list[str]:
    return discovery_sources.get(platform).suggest_keywords(seed, gl, hl)


def _existing_pipeline_keys(session: Session) -> set[str]:
    rows = session.exec(select(ChannelPipeline.channel_url)).all()
    return {normalize_channel_key(u) for u in rows}


def _actioned_channel_ids(session: Session) -> set[str]:
    """Channels the user already added or skipped, globally — excluded
    from every future scan regardless of which keyword finds them again."""
    rows = session.exec(select(DiscoveryCandidate.channel_id)).all()
    return set(rows)


def _update_progress(scan_run_id: int, done: int, total: int) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(DiscoveryScanRun, scan_run_id)
        if run is None:
            return
        run.channels_total = total
        run.channels_done = done
        s.add(run)
        s.commit()


def _save_result(scan_run_id: int, ref: ChannelRef, keyword: str, data: ChannelData, value: float) -> None:
    with Session(_api_db.engine) as s:
        s.add(
            DiscoveryScanResult(
                scan_run_id=scan_run_id,
                channel_id=ref.channel_id,
                channel_url=ref.channel_url,
                channel_title=ref.channel_title,
                found_via_keyword=keyword,
                subscriber_count=data.subscriber_count,
                median_view_count=data.median_view_count,
                days_since_last_video=data.days_since_last_video,
                latest_video_posted_at=data.latest_video_posted_at,
                cadence_videos_days_per_video=data.cadence_long_days_per_video,
                cadence_shorts_days_per_video=data.cadence_short_days_per_video,
                score=value,
                channel_description=data.channel_description,
                median_duration_seconds=data.median_duration_seconds,
            )
        )
        s.commit()


def _finalize_ok(scan_run_id: int, summary: ScanSummary) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(DiscoveryScanRun, scan_run_id)
        if run is None:
            return
        run.finished_at = now_utc()
        run.status = "done"
        run.channels_total = summary.channels_total
        run.channels_done = summary.channels_scanned
        run.candidates_found = summary.candidates_found
        s.add(run)
        s.commit()


def _finalize_error(scan_run_id: int, error_text: str) -> None:
    with Session(_api_db.engine) as s:
        run = s.get(DiscoveryScanRun, scan_run_id)
        if run is None:
            return
        run.finished_at = now_utc()
        run.status = "error"
        run.error_text = error_text[:2000]
        s.add(run)
        s.commit()


def run_scan(
    scan_run_id: int,
    platform: str,
    keywords: list[str],
    gl: str,
    hl: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> ScanSummary:
    """Search every keyword on `platform`, screen + score every unique
    channel found, and save survivors as DiscoveryScanResult rows for
    `scan_run_id`. Writes its own final status ('done'/'error') onto the
    DiscoveryScanRun row — the caller (api.routers.discovery's background
    task) does not need to. progress_cb(done, total) is additionally called
    after each channel so a caller can react without polling the DB itself.
    """
    try:
        summary = _run_scan_body(scan_run_id, platform, keywords, gl, hl, progress_cb)
    except Exception as e:
        log.exception("discovery scan %d failed", scan_run_id)
        _finalize_error(scan_run_id, str(e))
        raise
    _finalize_ok(scan_run_id, summary)
    return summary


def _run_scan_body(
    scan_run_id: int,
    platform: str,
    keywords: list[str],
    gl: str,
    hl: str,
    progress_cb: Optional[Callable[[int, int], None]],
) -> ScanSummary:
    source = discovery_sources.get(platform)

    found_via: dict[str, str] = {}
    channels_by_id: dict[str, ChannelRef] = {}
    for kw, refs in source.search_channels(keywords, gl, hl).items():
        for ref in refs:
            if ref.channel_id not in channels_by_id:
                channels_by_id[ref.channel_id] = ref
                found_via[ref.channel_id] = kw

    with Session(_api_db.engine) as s:
        blocked_keys = _existing_pipeline_keys(s)
        blocked_ids = _actioned_channel_ids(s)

    candidates = [
        ref
        for ref in channels_by_id.values()
        if normalize_channel_key(ref.channel_url) not in blocked_keys and ref.channel_id not in blocked_ids
    ]

    total = len(candidates)
    scanned = 0
    found = 0
    rejected = 0
    silent_days = settings.get_silent_months() * 30

    def report(done: int, tot: int) -> None:
        _update_progress(scan_run_id, done, tot)
        if progress_cb:
            progress_cb(done, tot)

    for ref in candidates:
        report(scanned, total)
        scanned += 1

        data = source.fetch_channel(ref)
        if data is None or data.days_since_last_video > silent_days:
            rejected += 1
            continue

        _save_result(scan_run_id, ref, found_via[ref.channel_id], data, score(data, source.SINGLE_FEED))
        found += 1

    report(scanned, total)

    return ScanSummary(
        channels_total=total, channels_scanned=scanned, candidates_found=found, candidates_rejected=rejected
    )
