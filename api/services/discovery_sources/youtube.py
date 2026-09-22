"""YouTube discovery source — two-phase, via yt-dlp, no API key.

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
entirely to /videos) — and cadence is kept as two separate numbers, one per
tab, because Shorts cadence carries most of the score (see
api.services.discovery_sources.base.score).
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests
import yt_dlp

from api.services import discovery_settings as settings
from api.services.discovery_sources.base import ChannelData, ChannelRef, cadence_stats
from api.services.ytdlp_opts import extractor_args

log = logging.getLogger("api.services.discovery_sources.youtube")

SINGLE_FEED = False
MARKET_AWARE = True
SEARCH_ENABLED = True
SUPPORTS_SUGGESTIONS = True
PIPELINE_SOURCE_TYPE = "youtube"

_SEARCH_RESULTS = 40  # ytsearch40; ytsearch20 measured at ~15 unique channels.
_FLAT_PAGE_SIZE = 12
_PROBE_RETRIES = 3  # consecutive videos tried per probe point before giving up
_AUTOCOMPLETE_URL = "https://suggestqueries-clients6.youtube.com/complete/search"


# ---------- yt-dlp / HTTP wrappers (mocked directly in tests) ------------------


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


# ---------- search ------------------------------------------------------------


def search_channels(keywords: list[str], gl: str, hl: str) -> dict[str, list[ChannelRef]]:
    """One ytsearch per keyword; each returns one ChannelRef per unique
    channel among the top _SEARCH_RESULTS videos."""
    return {kw: _search_one(kw, hl) for kw in keywords}


def _search_one(keyword: str, hl: str) -> list[ChannelRef]:
    info = _flat_extract(f"ytsearch{_SEARCH_RESULTS}:{keyword}", lang=hl)
    seen: dict[str, ChannelRef] = {}
    for e in _entries(info):
        channel_id = e.get("channel_id")
        channel_url = e.get("channel_url")
        if not channel_id or not channel_url:
            continue
        seen.setdefault(
            channel_id,
            ChannelRef(
                channel_id=channel_id,
                channel_url=channel_url,
                channel_title=e.get("channel") or e.get("uploader") or channel_id,
            ),
        )
    return list(seen.values())


# ---------- phase 1: cheap flat screen ----------------------------------------


@dataclass
class Phase1Result:
    subscriber_count: int
    median_view_count: int
    video_entries: list[dict]
    shorts_entries: list[dict]
    channel_description: str
    median_duration_seconds: Optional[int]


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

    days_since_last, cadence = cadence_stats(newest_dt, oldest_dt, oldest_index)
    return (days_since_last, cadence, newest_dt)


@dataclass
class Phase2Result:
    days_since_last_video: float
    cadence_videos_days_per_video: Optional[float]
    cadence_shorts_days_per_video: Optional[float]
    latest_video_posted_at: Optional[datetime]


def fetch_phase2(video_entries: list[dict], shorts_entries: list[dict]) -> Phase2Result:
    """Cadence is tracked as two independent numbers, one per tab — Shorts
    cadence is weighted far above video cadence in the score.
    days_since_last_video/latest_video_posted_at come from whichever tab has
    the more recent video. If neither tab yields usable dates,
    days_since_last_video is +inf so the silence reject in the scan drops
    the candidate."""
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
    return Phase2Result(
        days_since_last,
        results["videos"][1] if "videos" in results else None,
        results["shorts"][1] if "shorts" in results else None,
        latest_posted_at,
    )


def fetch_channel(ref: ChannelRef) -> Optional[ChannelData]:
    p1 = fetch_phase1(ref.channel_url)
    if p1 is None:
        return None
    p2 = fetch_phase2(p1.video_entries, p1.shorts_entries)
    return ChannelData(
        subscriber_count=p1.subscriber_count,
        median_view_count=p1.median_view_count,
        days_since_last_video=p2.days_since_last_video,
        cadence_short_days_per_video=p2.cadence_shorts_days_per_video,
        cadence_long_days_per_video=p2.cadence_videos_days_per_video,
        latest_video_posted_at=p2.latest_video_posted_at,
        channel_description=p1.channel_description,
        median_duration_seconds=p1.median_duration_seconds,
    )
