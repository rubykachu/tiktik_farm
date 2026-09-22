"""Douyin discovery source.

Both the keyword search and a creator's post list go through
vendor/douyin-downloader's API client, driven by douyin_bridge.py in a
subprocess — see that file for why it cannot be imported directly.

Douyin refuses anonymous requests, so this source needs a Douyin cookie.
It uses the most recently updated row of the Douyin cookies screen, the
same cookies the Douyin pipelines download with; when there is none, a scan
fails with a message saying to add one. Douyin serves mainland China only,
so the market picked in the UI does not apply, and it has one feed, so
there is one cadence.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

import api.db as _api_db
from api.models import DouyinCookie
from api.services import discovery_settings as settings
from api.services import secrets
from api.services.discovery_sources.base import ChannelData, ChannelRef, feed_stats

log = logging.getLogger("api.services.discovery_sources.douyin")

SINGLE_FEED = True
MARKET_AWARE = False
# Douyin refuses keyword search to anyone without a logged-in session
# (status_code 2483, "请先登录，再继续搜索吧"), and the cookie importer keeps
# no session cookie. Everything below is written and tested; it is parked
# until that decision is made — see backlogs/douyin-keyword-search/handover.md.
SEARCH_ENABLED = False
SUPPORTS_SUGGESTIONS = False
PIPELINE_SOURCE_TYPE = "douyin"

_FEED_PAGE_SIZE = 12
# Videos pulled per keyword before the unique creators behind them are kept.
_SEARCH_ITEMS = 40
_BRIDGE = os.path.join(os.path.dirname(__file__), "douyin_bridge.py")
_VENDOR_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "vendor", "douyin-downloader")
)
_BRIDGE_TIMEOUT = 300


class DouyinSearchError(Exception):
    """Douyin could not be searched — usually a missing or expired cookie."""


def _cookies() -> dict:
    with Session(_api_db.engine) as s:
        row = s.exec(select(DouyinCookie).order_by(DouyinCookie.updated_at.desc())).first()
    if row is None:
        raise DouyinSearchError("Chưa có cookie Douyin nào — thêm cookie ở màn hình Douyin cookies trước")
    return json.loads(secrets.decrypt(row.cookie_json_encrypted) or "{}")


def _run_bridge(request: dict) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, _BRIDGE, _VENDOR_DIR],
            input=json.dumps({**request, "cookies": _cookies()}),
            capture_output=True,
            text=True,
            timeout=_BRIDGE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise DouyinSearchError(f"Douyin không trả lời trong {_BRIDGE_TIMEOUT}s") from e

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise DouyinSearchError(f"Douyin bridge không trả về JSON: {proc.stderr[:400]}") from e
    if payload.get("error"):
        raise DouyinSearchError(payload["error"])
    return payload


# ---------- keyword suggestion ------------------------------------------------


def suggest_keywords(seed: str, gl: str, hl: str) -> list[str]:
    """Douyin's web API has no search-box autocomplete endpoint the vendor
    client exposes, so keywords are typed by hand here."""
    return []


# ---------- search ------------------------------------------------------------


def search_channels(keywords: list[str], gl: str, hl: str) -> dict[str, list[ChannelRef]]:
    """A keyword Douyin refused is reported per keyword; the scan only fails
    when every keyword failed, so one bad term does not throw away the rest
    of the batch. Douyin's own status_code/status_msg is carried through
    rather than collapsed into an empty result — "请先登录" (the cookie has no
    logged-in session) and 403 (the cookie expired) need different fixes,
    and both would otherwise look like "0 ứng viên"."""
    payload = _run_bridge({"mode": "search", "keywords": list(keywords), "max_items": _SEARCH_ITEMS})
    results = payload.get("results") or {}
    errors = payload.get("errors") or {}
    out: dict[str, list[ChannelRef]] = {}
    for kw in keywords:
        out[kw] = [
            ChannelRef(
                channel_id=c["channel_id"],
                channel_url=c["channel_url"],
                channel_title=c.get("nickname") or c["channel_id"],
                subscriber_count=int(c.get("follower_count") or 0),
                channel_description=c.get("signature") or "",
            )
            for c in results.get(kw) or []
        ]
    if errors and not any(out.values()):
        raise DouyinSearchError("; ".join(f"{kw}: {msg}" for kw, msg in errors.items()))
    for kw, msg in errors.items():
        log.warning("Douyin search failed for keyword %r: %s", kw, msg)
    return out


# ---------- metrics -----------------------------------------------------------


def fetch_channel(ref: ChannelRef) -> Optional[ChannelData]:
    """Returns None when the channel is rejected (below min_subscribers) or
    its profile yields no usable posts."""
    try:
        payload = _run_bridge({"mode": "metrics", "sec_uid": ref.channel_id, "count": _FEED_PAGE_SIZE})
    except DouyinSearchError:
        log.warning("Douyin metrics failed for %s", ref.channel_url, exc_info=True)
        return None

    # The search response carries a follower count for some creators only,
    # so the profile call is what makes the min-subscribers check reliable.
    subscriber_count = int(payload.get("follower_count") or ref.subscriber_count or 0)
    if subscriber_count < settings.get_min_subscribers():
        return None

    posts = [p for p in payload.get("posts") or [] if p.get("create_time")]
    if not posts:
        return None

    views = [int(p.get("play_count") or 0) for p in posts]
    durations = [int(p["duration_seconds"]) for p in posts if p.get("duration_seconds")]
    dated = sorted((datetime.fromtimestamp(p["create_time"], tz=timezone.utc) for p in posts), reverse=True)

    days_since_last, cadence, newest = feed_stats(dated)

    return ChannelData(
        subscriber_count=subscriber_count,
        median_view_count=int(statistics.median(views)) if views else 0,
        days_since_last_video=days_since_last,
        cadence_short_days_per_video=cadence,
        cadence_long_days_per_video=None,
        latest_video_posted_at=newest,
        channel_description=payload.get("signature") or ref.channel_description or "",
        median_duration_seconds=int(statistics.median(durations)) if durations else None,
    )
