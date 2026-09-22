"""TikTok creator listing and video download.

Mirrors api.services.youtube_channel — same three public functions, same
shapes — so the pipeline worker can pick either by source_type. The yt-dlp
calls themselves live in api.services.ytdlp_source.

Two things differ from YouTube:

- `sync_from_date` is applied here, not handed to yt-dlp. A TikTok flat
  listing already carries `timestamp` on every entry (YouTube's does not),
  so filtering locally costs nothing and does not depend on `dateafter`
  being honoured by this extractor.
- There is no transcript. TikTok's extractor yields no subtitles, so
  `download` returns "" for it and the LLM rewrite works from the title and
  description alone — the same as the Douyin pipeline.
- Every request impersonates a browser TLS fingerprint. TikTok answers an
  ordinary Python fingerprint with a stripped page and no error, which
  arrives here as an empty listing and reads like a channel that stopped
  posting. See api.services.ytdlp_source.impersonating_opts.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from api.services.tiktok_validate import is_tiktok_url
from api.services.ytdlp_source import DownloadError, VideoTooLong, download_video, extract_info, list_entries

__all__ = ["DownloadError", "VideoTooLong", "download", "get_video_info", "list_top_n", "video_url"]


def video_url(video_id: str) -> str:
    """Canonical URL for a bare post id. TikTok's own URLs embed the
    creator's handle, but the handle-less form resolves to the same post and
    is all a flat listing entry leaves us when it carries no URL."""
    return f"https://www.tiktok.com/@/video/{video_id}"


def _posted_on_or_after(entry: dict, cutoff: date) -> bool:
    """True when the entry has no usable post time — an undated entry is
    kept rather than silently dropped, since dropping it would make the
    pipeline skip a video it should have collected."""
    timestamp = entry.get("timestamp")
    if not timestamp:
        return True
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date() >= cutoff


def list_top_n(channel_url: str, n: int = 5, sync_from_date: Optional[date] = None) -> list[dict]:
    """List the top N posts from a TikTok creator URL.

    Returns list of dicts with keys: id, title, url.
    """
    if not is_tiktok_url(channel_url):
        raise ValueError(f"Not a valid TikTok URL: {channel_url}")

    entries = list_entries(channel_url, n, impersonate=True)

    results = []
    for entry in entries:
        if sync_from_date is not None and not _posted_on_or_after(entry, sync_from_date):
            continue
        video_id = entry.get("id", "")
        results.append({
            "id": video_id,
            "title": entry.get("title", ""),
            "url": entry.get("url") or entry.get("webpage_url") or video_url(video_id),
        })
        if len(results) >= n:
            break

    return results


def get_video_info(video_url_: str) -> dict:
    """Fetch title and description without downloading. Returns {title, description}."""
    if not is_tiktok_url(video_url_):
        raise ValueError(f"Not a valid TikTok URL: {video_url_}")
    info = extract_info(video_url_)
    if not info:
        return {"title": "", "description": ""}
    return {
        "title": str(info.get("title") or ""),
        "description": str(info.get("description") or ""),
    }


def download(
    video_url_: str,
    output_dir: str,
    max_duration_seconds: Optional[int] = None,
) -> tuple[str, str, str]:
    """Download a single post and return ``(file_path, description, transcript)``.

    transcript is always "" — see the module docstring.

    Raises:
        VideoTooLong: if video duration exceeds 3 * max_duration_seconds.
        DownloadError: if yt-dlp fails for any other reason.
    """
    if not is_tiktok_url(video_url_):
        raise ValueError(f"Not a valid TikTok URL: {video_url_}")

    file_path, info = download_video(
        video_url_, output_dir, max_duration_seconds, impersonate=True
    )
    return file_path, str(info.get("description") or ""), ""
