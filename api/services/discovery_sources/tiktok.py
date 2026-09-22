"""TikTok discovery source.

Search goes through tiktok_search.js, a headless-Chromium bridge — see that
file for why nothing cheaper works. It returns each creator's follower
count and bio directly, so no extra request is needed for them.

Metrics are one yt-dlp flat listing of the creator's feed. Unlike
YouTube's, TikTok's flat entries already carry `timestamp`, `view_count`
and `duration`, so recency, cadence, post time-of-day and median duration
all come out of that single call — there is no second, per-video phase.
TikTok has one feed, so there is one cadence.

That listing is asked for by `tiktokuser:<secUid>` rather than by the
profile URL. Both reach the same feed endpoint, but the URL form first
scrapes the profile page to recover the secUid from it, and TikTok does not
always serve a page carrying it — the failure is
"Unable to extract secondary user ID", and yt-dlp's own error text points
at this input form as the way round it. The search response already gave us
the secUid, so that scrape is pure risk. The profile URL stays as a
fallback for a candidate whose secUid did not come through.

The listing has to impersonate a browser TLS fingerprint, which needs
`curl_cffi` (pinned in requirements.txt). Without it TikTok answers with a
page stripped of the data and no error at all — see
api.services.ytdlp_source.impersonating_opts.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import yt_dlp

from api.services import discovery_settings as settings
from api.services.discovery_sources.base import ChannelData, ChannelRef, feed_stats
from api.services.ytdlp_source import SOCKET_TIMEOUT_SECONDS, impersonating_opts

log = logging.getLogger("api.services.discovery_sources.tiktok")

SINGLE_FEED = True
MARKET_AWARE = True
SEARCH_ENABLED = True
SUPPORTS_SUGGESTIONS = True
PIPELINE_SOURCE_TYPE = "tiktok"

_FLAT_PAGE_SIZE = 12
# TikTok throttles the feed endpoint per caller and answers a throttled
# request with an empty listing rather than an error, so an empty result is
# retried before the candidate is written off. Measured: a scan that lists
# one feed after another starts coming back empty partway through, and the
# same feed answers normally again seconds later — without this, those
# channels silently drop out of the results and look like dead accounts.
_FEED_ATTEMPTS = 3
_FEED_BACKOFF_SECONDS = 5
_BRIDGE = os.path.join(os.path.dirname(__file__), "tiktok_search.js")
# playwright-chromium is installed for the signing subprocess, not for this
# directory, so the bridge resolves it through NODE_PATH.
_NODE_MODULES = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "tiktok_uploader", "tiktok-signature", "node_modules"
)
# A browser run covers a warm-up plus up to three navigations per keyword
# (tiktok_search.js retries a search TikTok did not answer). Five keywords,
# the per-scan cap, all retrying still fits inside this.
_BRIDGE_TIMEOUT = 600


class TikTokSearchError(Exception):
    """The headless-browser bridge could not produce search results."""


def _run_bridge(request: dict) -> dict:
    env = {**os.environ, "NODE_PATH": os.path.abspath(_NODE_MODULES)}
    try:
        proc = subprocess.run(
            ["node", _BRIDGE],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=_BRIDGE_TIMEOUT,
            env=env,
        )
    except FileNotFoundError as e:
        raise TikTokSearchError("node is not installed in this environment") from e
    except subprocess.TimeoutExpired as e:
        raise TikTokSearchError(f"TikTok search timed out after {_BRIDGE_TIMEOUT}s") from e

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise TikTokSearchError(f"TikTok search bridge returned no JSON: {proc.stderr[:400]}") from e
    if payload.get("error"):
        raise TikTokSearchError(payload["error"])
    return payload


# ---------- keyword suggestion ------------------------------------------------


def suggest_keywords(seed: str, gl: str, hl: str) -> list[str]:
    """TikTok's own search-box suggestions. Returns [] on any failure —
    this is a convenience list, not a required step."""
    try:
        return _run_bridge({"mode": "suggest", "seed": seed, "language": hl}).get("suggestions", [])
    except TikTokSearchError:
        log.warning("TikTok keyword suggestions failed", exc_info=True)
        return []


# ---------- search ------------------------------------------------------------


def _to_refs(creators: list[dict]) -> list[ChannelRef]:
    refs = []
    for c in creators:
        unique_id = c.get("unique_id")
        channel_id = c.get("channel_id")
        if not unique_id or not channel_id:
            continue
        refs.append(
            ChannelRef(
                channel_id=str(channel_id),
                channel_url=f"https://www.tiktok.com/@{unique_id}",
                channel_title=c.get("nickname") or unique_id,
                subscriber_count=int(c.get("follower_count") or 0),
                channel_description=c.get("signature") or "",
                feed_key=c.get("sec_uid") or None,
            )
        )
    return refs


def search_channels(keywords: list[str], gl: str, hl: str) -> dict[str, list[ChannelRef]]:
    """One browser run for the whole batch. A keyword TikTok refused is
    reported per keyword; the scan only fails when every keyword failed, so
    one bad term does not throw away the rest of the batch."""
    payload = _run_bridge({"mode": "search", "keywords": list(keywords), "language": hl})
    results = payload.get("results") or {}
    errors = payload.get("errors") or {}
    out = {kw: _to_refs(results.get(kw) or []) for kw in keywords}
    if errors and not any(out.values()):
        raise TikTokSearchError("; ".join(f"{kw}: {msg}" for kw, msg in errors.items()))
    for kw, msg in errors.items():
        log.warning("TikTok search failed for keyword %r: %s", kw, msg)
    return out


# ---------- metrics -----------------------------------------------------------


def _flat_extract(url: str) -> Optional[dict]:
    opts = {
        "extract_flat": "in_playlist",
        "playlistend": _FLAT_PAGE_SIZE,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        **impersonating_opts(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        log.warning("flat extract failed for %s", url, exc_info=True)
        return None


def _feed_entries(ref: ChannelRef) -> list[dict]:
    """The creator's latest posts, by secUid when the search gave one and by
    profile URL otherwise. Each attempt tries both forms; an empty result is
    retried with a doubling backoff because that is what throttling looks
    like here. Returns [] once the attempts are spent — the caller then
    drops the candidate."""
    inputs = [f"tiktokuser:{ref.feed_key}"] if ref.feed_key else []
    inputs.append(ref.channel_url)

    for attempt in range(_FEED_ATTEMPTS):
        for url in inputs:
            entries = [e for e in ((_flat_extract(url) or {}).get("entries") or []) if e]
            if entries:
                return entries
        if attempt < _FEED_ATTEMPTS - 1:
            time.sleep(_FEED_BACKOFF_SECONDS * (2**attempt))
    log.warning("no feed entries for %s after %d attempts", ref.channel_url, _FEED_ATTEMPTS)
    return []


def fetch_channel(ref: ChannelRef) -> Optional[ChannelData]:
    """Returns None when the channel is rejected (below min_subscribers) or
    its profile yields no usable posts."""
    if (ref.subscriber_count or 0) < settings.get_min_subscribers():
        return None  # reject before spending the profile request

    entries = _feed_entries(ref)
    if not entries:
        return None

    views = [e["view_count"] for e in entries if e.get("view_count") is not None]
    durations = [e["duration"] for e in entries if e.get("duration") is not None]
    dated = sorted(
        (datetime.fromtimestamp(e["timestamp"], tz=timezone.utc) for e in entries if e.get("timestamp")),
        reverse=True,
    )
    days_since_last, cadence, newest = feed_stats(dated)

    return ChannelData(
        subscriber_count=int(ref.subscriber_count or 0),
        median_view_count=int(statistics.median(views)) if views else 0,
        days_since_last_video=days_since_last,
        cadence_short_days_per_video=cadence,
        cadence_long_days_per_video=None,
        latest_video_posted_at=newest,
        channel_description=ref.channel_description or "",
        median_duration_seconds=int(statistics.median(durations)) if durations else None,
    )
