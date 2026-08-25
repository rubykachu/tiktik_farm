"""Two-phase YouTube channel discovery via yt-dlp — no API key.

This is NOT an auto-picker: it rounds up candidate channels for a keyword
search and ranks them, and api.routers.discovery / the webapp let the user
add or skip each one by hand. There is no automatic scheduling — every scan
is triggered by the "Quét ngay" button with an explicit keyword set + market,
and runs as an API background task (see api.routers.discovery.scan_now).

Phase 1 (cheap, ~0.8s/channel): one flat listing of the channel's /videos
tab gives `channel_follower_count` (subscriber count) and per-video
`view_count`/`duration` for free. A channel below `min_subscribers` is
rejected here, before any more requests are spent on it. Channels with no
/videos tab (shorts-only channels) fall back to the /shorts tab flat
listing for the same fields.

Phase 2 (expensive, ~2.7-5.4s/channel, survivors only): flat listings never
populate `upload_date`/`timestamp` (verified empirically — the keys exist
but the values are always None), so recency, posting cadence, and the
actual post time-of-day require opening a few individual videos with a full
(non-flat) extraction. Both the /videos and /shorts tabs are probed —
YouTube channels are not consistently more active on one tab than the other
(measured: one channel posts far more often to /shorts, another almost
entirely to /videos) — and the tab with the tighter cadence wins.

Every scoring threshold used below (min subscribers, silence cutoff,
cadence target, score weights' reference scale) is read from
api.services.discovery_settings, never hardcoded. Market and the keyword
set are per-scan parameters instead, not settings.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
import yt_dlp
from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryScanResult, DiscoveryScanRun
from api.services import discovery_settings as settings
from api.services.ytdlp_opts import extractor_args

log = logging.getLogger("api.services.channel_discovery")

_SEARCH_RESULTS = 20  # ytsearch20 -> ~15 unique channels, measured.
_FLAT_PAGE_SIZE = 12
_PROBE_RETRIES = 3  # consecutive videos tried per probe point before giving up
_AUTOCOMPLETE_URL = "https://suggestqueries-clients6.youtube.com/complete/search"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------- keyword set normalization -----------------------------------------


def normalize_keyword_set(keywords: list[str]) -> str:
    """Canonical key for a batch of keywords, order/case/whitespace
    insensitive — re-scanning the same set overwrites the same
    DiscoveryScanRun row (see run_scan) instead of creating a new history
    entry."""
    cleaned = sorted({kw.strip().lower() for kw in keywords if kw.strip()})
    return "|".join(cleaned)


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


# ---------- yt-dlp wrappers (mocked directly in tests) ------------------------


def _flat_extract(url: str, playlistend: Optional[int] = None, lang: Optional[str] = None) -> Optional[dict]:
    opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        **extractor_args(),
    }
    if playlistend is not None:
        opts["playlistend"] = playlistend
    if lang:
        opts.setdefault("extractor_args", {}).setdefault("youtube", {})["lang"] = [lang]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        log.warning("flat extract failed for %s", url, exc_info=True)
        return None


def _full_extract(url: str) -> Optional[dict]:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, **extractor_args()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def _http_get_text(url: str, params: dict) -> Optional[str]:
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        log.warning("autocomplete request failed", exc_info=True)
        return None


def _entries(info: Optional[dict]) -> list[dict]:
    if not info:
        return []
    return [e for e in (info.get("entries") or []) if e]


def _entry_url(entry: dict) -> str:
    return entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id')}"


# ---------- keyword suggestion ------------------------------------------------


def suggest_keywords(seed: str, gl: str, hl: str) -> list[str]:
    """YouTube search-box autocomplete for a seed term. Returns [] on any
    network/parse failure rather than raising — this is a convenience list,
    not a required step."""
    raw = _http_get_text(_AUTOCOMPLETE_URL, {"client": "youtube", "ds": "yt", "q": seed, "hl": hl, "gl": gl})
    if not raw:
        return []
    start, end = raw.find("("), raw.rfind(")")
    if start == -1 or end == -1 or end <= start:
        return []

    try:
        data = json.loads(raw[start + 1 : end])
        return [item[0] for item in data[1] if item and item[0]]
    except (ValueError, IndexError, TypeError):
        log.warning("could not parse autocomplete response", exc_info=True)
        return []


# ---------- phase 1: cheap flat screen ----------------------------------------


@dataclass
class Phase1Result:
    subscriber_count: int
    median_view_count: int
    has_shorts_tab: bool
    recent_shorts_count: int
    video_entries: list[dict]
    shorts_entries: list[dict]
    channel_description: str
    median_duration_seconds: Optional[int]


def search_channel_candidates(keyword: str, hl: str) -> list[dict]:
    """ytsearch the keyword, return one {channel_id, channel_url,
    channel_title} dict per unique channel among the top _SEARCH_RESULTS
    videos."""
    info = _flat_extract(f"ytsearch{_SEARCH_RESULTS}:{keyword}", lang=hl)
    seen: dict[str, dict] = {}
    for e in _entries(info):
        channel_id = e.get("channel_id")
        channel_url = e.get("channel_url")
        if not channel_id or not channel_url:
            continue
        seen.setdefault(
            channel_id,
            {
                "channel_id": channel_id,
                "channel_url": channel_url,
                "channel_title": e.get("channel") or e.get("uploader") or channel_id,
            },
        )
    return list(seen.values())


def fetch_phase1(channel_base_url: str) -> Optional[Phase1Result]:
    """Returns None when the channel is rejected (below min_subscribers, or
    neither tab yields any data)."""
    base = channel_base_url.rstrip("/")
    videos_info = _flat_extract(f"{base}/videos", playlistend=_FLAT_PAGE_SIZE)
    video_entries = _entries(videos_info)

    shorts_info: Optional[dict] = None
    shorts_entries: list[dict] = []

    if video_entries:
        subscriber_count = int(videos_info.get("channel_follower_count") or 0)
        views = [e["view_count"] for e in video_entries if e.get("view_count") is not None]
        median_view = int(statistics.median(views)) if views else 0
        durations = [e["duration"] for e in video_entries if e.get("duration") is not None]
        channel_description = str(videos_info.get("description") or "")
    else:
        # No /videos tab — try /shorts for the same free fields.
        shorts_info = _flat_extract(f"{base}/shorts", playlistend=_FLAT_PAGE_SIZE)
        shorts_entries = _entries(shorts_info)
        if not shorts_entries:
            return None  # neither tab usable
        subscriber_count = int(shorts_info.get("channel_follower_count") or 0)
        views = [e["view_count"] for e in shorts_entries if e.get("view_count") is not None]
        median_view = int(statistics.median(views)) if views else 0
        durations = [e["duration"] for e in shorts_entries if e.get("duration") is not None]
        channel_description = str(shorts_info.get("description") or "")

    if subscriber_count < settings.get_min_subscribers():
        return None  # reject before spending the /shorts request below

    if shorts_info is None:
        shorts_info = _flat_extract(f"{base}/shorts", playlistend=_FLAT_PAGE_SIZE)
        shorts_entries = _entries(shorts_info)

    return Phase1Result(
        subscriber_count=subscriber_count,
        median_view_count=median_view,
        has_shorts_tab=bool(shorts_entries),
        recent_shorts_count=len(shorts_entries),
        video_entries=video_entries,
        shorts_entries=shorts_entries,
        channel_description=channel_description,
        median_duration_seconds=int(statistics.median(durations)) if durations else None,
    )


# ---------- phase 2: expensive cadence + post-time probe ----------------------


def _probe_video_datetime(entries: list[dict], start_index: int) -> Optional[datetime]:
    """Open entries starting at start_index until one yields a real post
    time. Roughly 18% of individual videos fail (removed/private) at
    measurement time, so a single try is not reliable. Prefers `timestamp`
    (has time-of-day); falls back to `upload_date` at midnight UTC since a
    handful of videos carry one but not the other."""
    for i in range(start_index, min(start_index + _PROBE_RETRIES, len(entries))):
        info = _full_extract(_entry_url(entries[i]))
        if not info:
            continue
        ts = info.get("timestamp")
        if ts:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if info.get("upload_date"):
            return datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
    return None


def _cadence_from_tab(entries: list[dict]) -> Optional[tuple[float, float, datetime]]:
    """Returns (days_since_last_video, cadence_days_per_video, newest_dt)
    computed from the newest and oldest entries in one tab's flat page, or
    None if fewer than 2 usable dates were found."""
    if len(entries) < 2:
        return None

    newest_dt = _probe_video_datetime(entries, 0)
    if newest_dt is None:
        return None

    oldest_dt = None
    oldest_index = None
    last_index = len(entries) - 1
    for i in range(last_index, max(last_index - _PROBE_RETRIES, -1), -1):
        dt = _probe_video_datetime([entries[i]], 0)
        if dt is not None:
            oldest_dt, oldest_index = dt, i
            break
    if oldest_dt is None or oldest_index == 0:
        return None

    days_since_last = (now_utc() - newest_dt).total_seconds() / 86400
    span_days = (newest_dt - oldest_dt).total_seconds() / 86400
    cadence = max(span_days, 0.0) / oldest_index
    return (max(days_since_last, 0.0), cadence, newest_dt)


@dataclass
class Phase2Result:
    days_since_last_video: float
    cadence_days_per_video: Optional[float]
    cadence_tab: Optional[str]
    latest_video_posted_at: Optional[datetime]


def fetch_phase2(video_entries: list[dict], shorts_entries: list[dict]) -> Phase2Result:
    """cadence_tab is whichever tab produced the smaller (more frequent)
    cadence; days_since_last_video/latest_video_posted_at come from
    whichever tab has the more recent video, independent of which tab won
    on cadence. If neither tab yields usable dates, days_since_last_video
    is +inf so the silence reject in run_scan drops the candidate."""
    results: dict[str, tuple[float, float, datetime]] = {}
    videos_result = _cadence_from_tab(video_entries)
    if videos_result:
        results["videos"] = videos_result
    shorts_result = _cadence_from_tab(shorts_entries)
    if shorts_result:
        results["shorts"] = shorts_result

    if not results:
        return Phase2Result(float("inf"), None, None, None)

    most_recent_tab = min(results, key=lambda k: results[k][0])
    days_since_last = results[most_recent_tab][0]
    latest_posted_at = results[most_recent_tab][2]
    cadence_tab = min(results, key=lambda k: results[k][1])
    return Phase2Result(days_since_last, results[cadence_tab][1], cadence_tab, latest_posted_at)


# ---------- scoring ------------------------------------------------------------


@dataclass
class ChannelMetrics:
    subscriber_count: int
    median_view_count: int
    has_shorts_tab: bool
    recent_shorts_count: int
    days_since_last_video: float
    cadence_days_per_video: Optional[float]


def score_candidate(metrics: ChannelMetrics) -> float:
    """0-100. Weighted toward posting regularity, not channel size:
    recency 30, cadence 35, absolute median view 15, has-Shorts +
    recent-Shorts-count 20.
    """
    silent_days = settings.get_silent_months() * 30
    recency = 30 * _clamp(1 - metrics.days_since_last_video / silent_days, 0, 1) if silent_days > 0 else 0.0

    target = settings.get_target_days_per_video()
    if metrics.cadence_days_per_video and metrics.cadence_days_per_video > 0 and target > 0:
        cadence = 35 * _clamp(target / metrics.cadence_days_per_video, 0, 1)
    else:
        cadence = 0.0

    ref = settings.get_view_score_reference()
    if ref > 0:
        view = 15 * _clamp(math.log10(metrics.median_view_count + 1) / math.log10(ref + 1), 0, 1)
    else:
        view = 0.0

    shorts = (10.0 if metrics.has_shorts_tab else 0.0) + 10 * _clamp(metrics.recent_shorts_count / 10, 0, 1)

    return round(recency + cadence + view + shorts, 1)


# ---------- orchestration --------------------------------------------------


@dataclass
class ScanSummary:
    channels_total: int
    channels_scanned: int
    candidates_found: int
    candidates_rejected: int


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


def _save_result(
    scan_run_id: int,
    channel: dict,
    keyword: str,
    metrics: ChannelMetrics,
    score: float,
    phase2: Phase2Result,
    p1: Phase1Result,
) -> None:
    with Session(_api_db.engine) as s:
        s.add(
            DiscoveryScanResult(
                scan_run_id=scan_run_id,
                channel_id=channel["channel_id"],
                channel_url=channel["channel_url"],
                channel_title=channel["channel_title"],
                found_via_keyword=keyword,
                subscriber_count=metrics.subscriber_count,
                median_view_count=metrics.median_view_count,
                days_since_last_video=metrics.days_since_last_video,
                latest_video_posted_at=phase2.latest_video_posted_at,
                cadence_days_per_video=metrics.cadence_days_per_video,
                cadence_tab=phase2.cadence_tab,
                has_shorts_tab=metrics.has_shorts_tab,
                recent_shorts_count=metrics.recent_shorts_count,
                score=score,
                channel_description=p1.channel_description,
                median_duration_seconds=p1.median_duration_seconds,
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
    keywords: list[str],
    gl: str,
    hl: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> ScanSummary:
    """Search every keyword, screen + score every unique channel found, and
    save survivors as DiscoveryScanResult rows for `scan_run_id`. Writes its
    own final status ('done'/'error') onto the DiscoveryScanRun row — the
    caller (api.routers.discovery's background task) does not need to.
    progress_cb(done, total) is additionally called after each channel so a
    caller can react without polling the DB itself.
    """
    try:
        summary = _run_scan_body(scan_run_id, keywords, gl, hl, progress_cb)
    except Exception as e:
        log.exception("discovery scan %d failed", scan_run_id)
        _finalize_error(scan_run_id, str(e))
        raise
    _finalize_ok(scan_run_id, summary)
    return summary


def _run_scan_body(
    scan_run_id: int,
    keywords: list[str],
    gl: str,
    hl: str,
    progress_cb: Optional[Callable[[int, int], None]],
) -> ScanSummary:
    found_via: dict[str, str] = {}
    channels_by_id: dict[str, dict] = {}
    for kw in keywords:
        for ch in search_channel_candidates(kw, hl):
            if ch["channel_id"] not in channels_by_id:
                channels_by_id[ch["channel_id"]] = ch
                found_via[ch["channel_id"]] = kw

    with Session(_api_db.engine) as s:
        blocked_keys = _existing_pipeline_keys(s)
        blocked_ids = _actioned_channel_ids(s)

    candidates = [
        ch
        for ch in channels_by_id.values()
        if normalize_channel_key(ch["channel_url"]) not in blocked_keys and ch["channel_id"] not in blocked_ids
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

    for ch in candidates:
        report(scanned, total)

        p1 = fetch_phase1(ch["channel_url"])
        if p1 is None:
            rejected += 1
            scanned += 1
            continue

        phase2 = fetch_phase2(p1.video_entries, p1.shorts_entries)
        if phase2.days_since_last_video > silent_days:
            rejected += 1
            scanned += 1
            continue

        metrics = ChannelMetrics(
            subscriber_count=p1.subscriber_count,
            median_view_count=p1.median_view_count,
            has_shorts_tab=p1.has_shorts_tab,
            recent_shorts_count=p1.recent_shorts_count,
            days_since_last_video=phase2.days_since_last_video,
            cadence_days_per_video=phase2.cadence_days_per_video,
        )
        score = score_candidate(metrics)
        _save_result(scan_run_id, ch, found_via[ch["channel_id"]], metrics, score, phase2, p1)
        found += 1
        scanned += 1

    report(scanned, total)

    return ScanSummary(
        channels_total=total, channels_scanned=scanned, candidates_found=found, candidates_rejected=rejected
    )
