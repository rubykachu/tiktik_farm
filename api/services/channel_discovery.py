"""Two-phase YouTube channel discovery via yt-dlp — no API key.

This is NOT an auto-picker: it rounds up candidate channels for a keyword
and ranks them, and api.routers.discovery / the webapp let the user
approve or skip each one by hand.

Phase 1 (cheap, ~0.8s/channel): one flat listing of the channel's /videos
tab gives `channel_follower_count` (subscriber count) and per-video
`view_count`/`duration` for free. A channel below `min_subscribers` is
rejected here, before any more requests are spent on it. Channels with no
/videos tab (shorts-only channels) fall back to the /shorts tab flat
listing for the same fields.

Phase 2 (expensive, ~2.7-5.4s/channel, survivors only): `upload_date` is
NOT present in flat listings (verified empirically — the key exists but the
value is always None), so recency and posting cadence require opening a
few individual videos with a full (non-flat) extraction. Both the /videos
and /shorts tabs are probed — YouTube channels are not consistently more
active on one tab than the other (measured: one channel posts far more
often to /shorts, another almost entirely to /videos) — and the tab with
the tighter cadence wins.

Every threshold used below (min subscribers, silence cutoff, cadence
target, score weights' reference scale) is read from
api.services.discovery_settings, never hardcoded.
"""
from __future__ import annotations

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
from api.models import AppSetting, ChannelPipeline, DiscoveryCandidate
from api.services import discovery_settings as settings
from api.services.ytdlp_opts import extractor_args

log = logging.getLogger("api.services.channel_discovery")

_SEARCH_RESULTS = 20  # ytsearch20 -> ~15 unique channels, measured.
_FLAT_PAGE_SIZE = 12
_PROBE_RETRIES = 3  # consecutive videos tried per probe point before giving up
_AUTOCOMPLETE_URL = "https://suggestqueries-clients6.youtube.com/complete/search"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------- dedupe -----------------------------------------------------------


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
    import json

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
    else:
        # No /videos tab — try /shorts for the same free fields.
        shorts_info = _flat_extract(f"{base}/shorts", playlistend=_FLAT_PAGE_SIZE)
        shorts_entries = _entries(shorts_info)
        if not shorts_entries:
            return None  # neither tab usable
        subscriber_count = int(shorts_info.get("channel_follower_count") or 0)
        views = [e["view_count"] for e in shorts_entries if e.get("view_count") is not None]
        median_view = int(statistics.median(views)) if views else 0

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
    )


# ---------- phase 2: expensive cadence probe ----------------------------------


def _probe_upload_date(entries: list[dict], start_index: int) -> Optional[str]:
    """Open entries starting at start_index until one yields upload_date.
    Roughly 18% of individual videos fail (removed/private) at measurement
    time, so a single try is not reliable."""
    for i in range(start_index, min(start_index + _PROBE_RETRIES, len(entries))):
        info = _full_extract(_entry_url(entries[i]))
        if info and info.get("upload_date"):
            return info["upload_date"]
    return None


def _cadence_from_tab(entries: list[dict]) -> Optional[tuple[float, float]]:
    """Returns (days_since_last_video, cadence_days_per_video) computed from
    the newest and oldest entries in one tab's flat page, or None if fewer
    than 2 usable dates were found."""
    if len(entries) < 2:
        return None

    newest = _probe_upload_date(entries, 0)
    if newest is None:
        return None

    oldest = None
    oldest_index = None
    last_index = len(entries) - 1
    for i in range(last_index, max(last_index - _PROBE_RETRIES, -1), -1):
        info = _full_extract(_entry_url(entries[i]))
        if info and info.get("upload_date"):
            oldest, oldest_index = info["upload_date"], i
            break
    if oldest is None or oldest_index == 0:
        return None

    newest_dt = datetime.strptime(newest, "%Y%m%d").replace(tzinfo=timezone.utc)
    oldest_dt = datetime.strptime(oldest, "%Y%m%d").replace(tzinfo=timezone.utc)
    days_since_last = (now_utc() - newest_dt).total_seconds() / 86400
    span_days = (newest_dt - oldest_dt).total_seconds() / 86400
    cadence = max(span_days, 0.0) / oldest_index
    return (max(days_since_last, 0.0), cadence)


def fetch_phase2(video_entries: list[dict], shorts_entries: list[dict]) -> tuple[float, Optional[float], Optional[str]]:
    """Returns (days_since_last_video, cadence_days_per_video, cadence_tab).
    cadence_tab is whichever tab produced the smaller (more frequent)
    cadence; days_since_last_video is the smaller (more recent) value across
    both tabs. If neither tab yields usable dates, days_since_last_video is
    +inf so the silence reject in run_scan drops the candidate."""
    results: dict[str, tuple[float, float]] = {}
    videos_result = _cadence_from_tab(video_entries)
    if videos_result:
        results["videos"] = videos_result
    shorts_result = _cadence_from_tab(shorts_entries)
    if shorts_result:
        results["shorts"] = shorts_result

    if not results:
        return (float("inf"), None, None)

    days_since_last = min(r[0] for r in results.values())
    best_tab = min(results, key=lambda k: results[k][1])
    return (days_since_last, results[best_tab][1], best_tab)


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
    """0-100. Weighted toward posting regularity, not channel size — see
    the "Công thức chấm" design note relayed to the user: recency 30,
    cadence 35, absolute median view 15, has-Shorts + recent-Shorts-count 20.
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


# ---------- manual "scan now" trigger ------------------------------------

_RUN_NOW_KEY = "discovery.run_now_requested_at"


def request_run_now() -> None:
    """Called by the 'Quét ngay' button. Row presence = requested — the
    scheduler's discovery_tick clears it once it starts the scan, mirroring
    ChannelPipeline.run_now_requested_at for the per-pipeline 'run now'."""
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, _RUN_NOW_KEY)
        if row is None:
            row = AppSetting(key=_RUN_NOW_KEY, value=now_utc().isoformat())
        else:
            row.value = now_utc().isoformat()
        s.add(row)
        s.commit()


def is_run_now_requested() -> bool:
    with Session(_api_db.engine) as s:
        return s.get(AppSetting, _RUN_NOW_KEY) is not None


def clear_run_now_requested() -> None:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, _RUN_NOW_KEY)
        if row is not None:
            s.delete(row)
            s.commit()


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


def _blocked_candidate_ids(session: Session) -> set[str]:
    rows = session.exec(
        select(DiscoveryCandidate.channel_id).where(DiscoveryCandidate.status.in_(["skipped", "added"]))
    ).all()
    return set(rows)


def _upsert_candidate(channel: dict, keyword: str, metrics: ChannelMetrics, score: float, cadence_tab: Optional[str]) -> None:
    with Session(_api_db.engine) as s:
        row = s.exec(
            select(DiscoveryCandidate).where(DiscoveryCandidate.channel_id == channel["channel_id"])
        ).first()
        if row is None:
            row = DiscoveryCandidate(channel_id=channel["channel_id"], status="new")
        row.channel_url = channel["channel_url"]
        row.channel_title = channel["channel_title"]
        row.found_via_keyword = keyword
        row.subscriber_count = metrics.subscriber_count
        row.median_view_count = metrics.median_view_count
        row.days_since_last_video = metrics.days_since_last_video
        row.cadence_days_per_video = metrics.cadence_days_per_video
        row.cadence_tab = cadence_tab
        row.has_shorts_tab = metrics.has_shorts_tab
        row.recent_shorts_count = metrics.recent_shorts_count
        row.score = score
        row.last_scanned_at = now_utc()
        s.add(row)
        s.commit()


def run_scan(
    keywords: list[str],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> ScanSummary:
    """Search every keyword, screen + score every unique channel found, and
    upsert survivors into discovery_candidates. progress_cb(done, total) is
    called after each channel so a caller (the scheduler tick) can persist
    progress for the webapp to poll."""
    gl, hl = settings.get_market()

    found_via: dict[str, str] = {}
    channels_by_id: dict[str, dict] = {}
    for kw in keywords:
        for ch in search_channel_candidates(kw, hl):
            if ch["channel_id"] not in channels_by_id:
                channels_by_id[ch["channel_id"]] = ch
                found_via[ch["channel_id"]] = kw

    with Session(_api_db.engine) as s:
        blocked_keys = _existing_pipeline_keys(s)
        blocked_ids = _blocked_candidate_ids(s)

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

    for ch in candidates:
        if progress_cb:
            progress_cb(scanned, total)

        p1 = fetch_phase1(ch["channel_url"])
        if p1 is None:
            rejected += 1
            scanned += 1
            continue

        days_since_last, cadence, cadence_tab = fetch_phase2(p1.video_entries, p1.shorts_entries)
        if days_since_last > silent_days:
            rejected += 1
            scanned += 1
            continue

        metrics = ChannelMetrics(
            subscriber_count=p1.subscriber_count,
            median_view_count=p1.median_view_count,
            has_shorts_tab=p1.has_shorts_tab,
            recent_shorts_count=p1.recent_shorts_count,
            days_since_last_video=days_since_last,
            cadence_days_per_video=cadence,
        )
        score = score_candidate(metrics)
        _upsert_candidate(ch, found_via[ch["channel_id"]], metrics, score, cadence_tab)
        found += 1
        scanned += 1

    if progress_cb:
        progress_cb(scanned, total)

    return ScanSummary(
        channels_total=total, channels_scanned=scanned, candidates_found=found, candidates_rejected=rejected
    )
