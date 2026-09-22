"""Which module a pipeline pulls its videos from, keyed by source_type.

YouTube and TikTok both go through yt-dlp and expose the same three
functions, so the worker calls whichever this returns and the rest of a tick
— dedupe, caption, upload, Telegram — is identical for either.

Douyin is not here. It does not fit this shape: it downloads through a
vendor subprocess, needs a cookie, and yields a different record shape, so
scheduler.pipeline_worker routes it to its own path before reaching this.

A module in here provides:

    list_top_n(channel_url, n, sync_from_date) -> [{id, title, url}]
    download(video_url, output_dir, max_duration_seconds)
        -> (file_path, description, transcript)
    get_video_info(video_url) -> {title, description}
    video_url(video_id) -> str
        The URL to use when a flat listing entry carries none of its own.
    VideoTooLong, DownloadError
        Raised by download; both modules raise the same classes.
"""
from __future__ import annotations

from types import ModuleType

from api.services import tiktok_channel, youtube_channel

SOURCES: dict[str, ModuleType] = {
    "youtube": youtube_channel,
    "tiktok": tiktok_channel,
}

PIPELINE_SOURCE_TYPES = tuple(SOURCES)


def get(source_type: str) -> ModuleType:
    """The module for `source_type`, defaulting to YouTube.

    The default is what keeps rows written before source_type existed
    working: they carry '' or a missing value and have always meant YouTube.
    """
    return SOURCES.get(source_type or "youtube", youtube_channel)
