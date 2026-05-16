"""Thin mockable seam around tiktok_uploader.tiktok.upload_video.

Both the API (immediate uploads) and the scheduler (due jobs) go through here.
Tests patch `perform_upload` rather than reaching into the uploader package,
which means no real network / subprocess fires in the test suite.

Publish metadata: `upload_video` returns the TikTok JSON body on success (status_code 0).
If that body has no ``item_id`` / share URL, we still report ok=True but url/id may be
None — some API responses are minimal or change shape over time.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from tiktok_uploader import tiktok as _tiktok

logger = logging.getLogger(__name__)

UploadResult = tuple[bool, Optional[str], Optional[str]]

_VIDEO_PATH_RE = re.compile(r"tiktok\.com/@[^/]+/video/(\d+)", re.IGNORECASE)


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
        item_id, url = _extract_publish_ids(result, username)
        if url:
            return (True, item_id, url)
        if item_id:
            return (True, item_id, f"https://www.tiktok.com/@{username}/video/{item_id}")

    if result is True or (isinstance(result, dict) and result):
        logger.warning("Upload succeeded but could not extract item_id or share URL from response")
        return (True, None, None)

    return (False, None, None)


def _extract_publish_ids(response: dict, username: str) -> tuple[Optional[str], Optional[str]]:
    """Return (item_id, tiktok_url). Prefer an explicit share URL from TikTok."""
    if not isinstance(response, dict):
        return None, None

    for key in ("share_url", "share_link", "share_link_url"):
        raw = response.get(key)
        if isinstance(raw, str) and "tiktok.com" in raw:
            url = raw.strip()
            vid = _video_id_from_url(url) or _find_first_str_key(response, ("item_id", "aweme_id"))
            return (vid, url)

    item_id = (
        _find_first_str_key(response, ("item_id", "aweme_id"))
        or _extract_item_id_flat(response)
    )
    return (item_id, "")


def _video_id_from_url(url: str) -> Optional[str]:
    m = _VIDEO_PATH_RE.search(url)
    return m.group(1) if m else None


def _find_first_str_key(obj: object, keys: tuple[str, ...], depth: int = 0) -> Optional[str]:
    """Depth-limited search for TikTok id fields in nested dict/list responses."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if v is not None and v != "":
                return str(v).strip() or None
        for v in obj.values():
            found = _find_first_str_key(v, keys, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_first_str_key(v, keys, depth + 1)
            if found:
                return found
    return None


def _extract_item_id_flat(response: dict) -> Optional[str]:
    """Try top-level / single ``data`` wrapper only (legacy paths)."""
    try:
        data = response.get("data") or response
        if not isinstance(data, dict):
            return None
        item_id = data.get("item_id") or data.get("aweme_id")
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
