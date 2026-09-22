"""Shapes and scoring shared by every discovery source.

A source module turns one keyword into candidate channels
(`search_channels`) and one candidate channel into the numbers the ranking
needs (`fetch_channel`). Everything else — deduping against existing
pipelines, the silence cutoff, persistence, progress reporting — belongs to
api.services.channel_discovery and is the same for all sources.

Every source module must expose:

    SINGLE_FEED: bool
        False only for YouTube, which splits its uploads across a /videos
        tab and a /shorts tab and therefore reports two cadences. TikTok and
        Douyin have one feed, so they fill cadence_short_days_per_video and
        leave cadence_long_days_per_video None.
    MARKET_AWARE: bool
        Whether the market (gl/hl) picked in the UI changes anything. Douyin
        serves mainland China only and ignores it.
    SEARCH_ENABLED: bool
        False parks a source that is written but cannot run yet. The API
        leaves it out of GET /api/discovery/platforms and refuses a scan for
        it, so it never reaches the platform picker — offering a choice that
        always fails is worse than not offering it. Flip it back to True in
        the source module; nothing else has to change.
    SUPPORTS_SUGGESTIONS: bool
        False when suggest_keywords can only ever return [], so the webapp
        hides the "Gợi ý" box instead of offering a button that does nothing.
    PIPELINE_SOURCE_TYPE: str | None
        The ChannelPipeline.source_type a candidate found here becomes, or
        None when pipelines cannot pull from this platform at all — then the
        webapp offers no "add to schedule" button for its candidates.
    suggest_keywords(seed, gl, hl) -> list[str]
    search_channels(keywords, gl, hl) -> dict[keyword, list[ChannelRef]]
        Takes the whole keyword batch at once: TikTok pays for a browser
        start-up per call and amortizes it across the batch.
    fetch_channel(ref) -> ChannelData | None      # None = rejected

Thresholds (min subscribers, silence cutoff, cadence target, view
reference) are read from api.services.discovery_settings, never hardcoded,
and are shared across sources.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from api.db import now_utc
from api.services import discovery_settings as settings


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def cadence_stats(newest: datetime, oldest: datetime, gaps: int) -> tuple[float, float]:
    """(days_since_last_video, days_per_video) from the newest and oldest
    post of one feed page and the number of gaps between them — 12 posts
    with both ends dated is 11 gaps. Only reflects recent posting, not the
    channel's whole history."""
    days_since_last = max((now_utc() - newest).total_seconds() / 86400, 0.0)
    span_days = max((newest - oldest).total_seconds() / 86400, 0.0)
    return days_since_last, span_days / gaps


def feed_stats(
    posts_newest_first: list[datetime],
) -> tuple[float, Optional[float], Optional[datetime]]:
    """(days_since_last_video, days_per_video, newest) for a single-feed
    source, from one page of its post times. days_since_last_video is +inf
    with no dated post at all, so the scan's silence cutoff drops the
    candidate; cadence needs two dated posts and is None below that.
    """
    if not posts_newest_first:
        return float("inf"), None, None
    newest = posts_newest_first[0]
    if len(posts_newest_first) < 2:
        days_since_last, _ = cadence_stats(newest, newest, 1)
        return days_since_last, None, newest
    days_since_last, cadence = cadence_stats(newest, posts_newest_first[-1], len(posts_newest_first) - 1)
    return days_since_last, cadence, newest


@dataclass
class ChannelRef:
    """One channel a keyword search turned up, before any metrics."""

    channel_id: str
    channel_url: str
    channel_title: str
    # TikTok's and Douyin's search responses already carry these, so their
    # fetch_channel does not spend a second request on them. YouTube's
    # search does not, and leaves them None.
    subscriber_count: Optional[int] = None
    channel_description: Optional[str] = None
    # An id the source can list the feed by when that is more reliable than
    # the channel URL. On TikTok it is the author's secUid, which lets
    # yt-dlp skip scraping the profile page. None when the source has none.
    feed_key: Optional[str] = None


@dataclass
class ChannelData:
    """Everything a scan stores for one channel. `cadence_short` is the
    channel's short-form feed (YouTube /shorts, the whole TikTok or Douyin
    feed); `cadence_long` is YouTube's /videos tab and stays None on
    single-feed sources."""

    subscriber_count: int
    median_view_count: int
    days_since_last_video: float
    cadence_short_days_per_video: Optional[float]
    cadence_long_days_per_video: Optional[float]
    latest_video_posted_at: Optional[datetime]
    channel_description: str
    median_duration_seconds: Optional[int]


# Point budget, summing to 100. A source with one feed gives the whole
# cadence budget to that feed, so its scores stay comparable with YouTube's
# instead of being capped at 80.
RECENCY_POINTS = 20
CADENCE_POINTS = 65
VIEW_POINTS = 15
# YouTube's split of CADENCE_POINTS: Shorts regularity is the signal this
# tool is actually looking for, so it carries most of the budget.
YOUTUBE_SHORT_POINTS = 45
YOUTUBE_LONG_POINTS = CADENCE_POINTS - YOUTUBE_SHORT_POINTS


def score(data: ChannelData, single_feed: bool) -> float:
    """0-100. Recency 20 + cadence 65 + median view 15.

    On YouTube the cadence budget splits 45 (Shorts) / 20 (video dài) and a
    channel missing one feed loses that bucket outright — there is no
    fallback between them. On a single-feed source the one cadence carries
    all 65. View only ever adds, never subtracts, so a low-view-but-regular
    channel still scores well and a viral one is not favoured for it (more
    views also means more copyright-strike exposure).
    """
    silent_days = settings.get_silent_months() * 30
    recency = (
        RECENCY_POINTS * clamp(1 - data.days_since_last_video / silent_days, 0, 1)
        if silent_days > 0
        else 0.0
    )

    target = settings.get_target_days_per_video()

    def cadence_score(days: Optional[float], weight: float) -> float:
        if days and days > 0 and target > 0:
            return weight * clamp(target / days, 0, 1)
        return 0.0

    if single_feed:
        cadence = cadence_score(data.cadence_short_days_per_video, CADENCE_POINTS)
    else:
        cadence = cadence_score(data.cadence_short_days_per_video, YOUTUBE_SHORT_POINTS) + cadence_score(
            data.cadence_long_days_per_video, YOUTUBE_LONG_POINTS
        )

    ref = settings.get_view_score_reference()
    if ref > 0:
        view = VIEW_POINTS * clamp(math.log10(data.median_view_count + 1) / math.log10(ref + 1), 0, 1)
    else:
        view = 0.0

    return round(recency + cadence + view, 1)
