"""Thin mockable seam around tiktok_uploader.tiktok.upload_video.

Both the API (immediate uploads) and the scheduler (due jobs) go through here.
Tests patch `perform_upload` rather than reaching into the uploader package,
which means no real network / subprocess fires in the test suite.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from tiktok_uploader import tiktok as _tiktok

logger = logging.getLogger(__name__)

UploadResult = tuple[bool, Optional[str], Optional[str]]


def perform_upload(
    username: str,
    video_path: str,
    title: str,
    *,
    allow_comment: int = 1,
    allow_duet: int = 0,
    allow_stitch: int = 0,
    visibility_type: int = 0,
    brand_organic_type: int = 0,
    branded_content_type: int = 0,
    ai_label: int = 0,
    proxy: str = "",
) -> UploadResult:
    """Delegate to the underlying uploader.

    Returns (ok, tiktok_video_id, tiktok_url).
    If parse fails but upload succeeded: (True, None, None) + warning log.
    """
    result = _tiktok.upload_video(
        username,
        video_path,
        title,
        0,  # schedule_time — unused, handled by our scheduler
        allow_comment,
        allow_duet,
        allow_stitch,
        visibility_type,
        brand_organic_type,
        branded_content_type,
        ai_label,
        proxy,
    )

    if not result:
        return (False, None, None)

    if isinstance(result, dict):
        item_id = _extract_item_id(result)
        if item_id:
            url = f"https://www.tiktok.com/@{username}/video/{item_id}"
            return (True, item_id, url)

    if result is True or (isinstance(result, dict) and result):
        logger.warning("Upload succeeded but could not extract item_id from response")
        return (True, None, None)

    return (False, None, None)


def _extract_item_id(response: dict) -> Optional[str]:
    """Try to extract item_id from TikTok upload response."""
    try:
        data = response.get("data") or response
        item_id = data.get("item_id") or data.get("video_id")
        if item_id:
            return str(item_id)
    except (AttributeError, TypeError):
        pass
    return None


def upload_from_options(
    username: str, video_path: str, title: str, options: dict[str, Any]
) -> UploadResult:
    """Convenience: fan out an options dict into perform_upload kwargs.

    Returns (ok, tiktok_video_id, tiktok_url).
    """
    return perform_upload(
        username,
        video_path,
        title,
        allow_comment=int(options.get("allow_comment", 1)),
        allow_duet=int(options.get("allow_duet", 0)),
        allow_stitch=int(options.get("allow_stitch", 0)),
        visibility_type=int(options.get("visibility_type", 0)),
        brand_organic_type=int(options.get("brand_organic_type", 0)),
        branded_content_type=int(options.get("branded_content_type", 0)),
        ai_label=int(options.get("ai_label", 0)),
        proxy=str(options.get("proxy", "")),
    )
