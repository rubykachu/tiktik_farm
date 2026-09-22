"""YouTube channel listing and video download.

The yt-dlp calls themselves live in api.services.ytdlp_source, which knows
nothing about YouTube; this module adds what is actually YouTube-specific —
refusing a non-YouTube URL, rebuilding a watch URL from a bare video id, and
reading subtitles, which only YouTube's extractor populates.

VideoTooLong and DownloadError are re-exported so callers that catch them
keep working against this module.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from api.services.transcript import fetch_transcript
from api.services.ytdlp_source import DownloadError, VideoTooLong, download_video, extract_info, list_entries
from api.services.youtube_validate import is_youtube_url

__all__ = ["DownloadError", "VideoTooLong", "download", "get_video_info", "list_top_n", "video_url"]


def video_url(video_id: str) -> str:
    """Watch URL for a bare video id — the fallback when a flat listing entry
    carries no URL of its own."""
    return f"https://www.youtube.com/watch?v={video_id}"


def list_top_n(channel_url: str, n: int = 5, sync_from_date: Optional[date] = None) -> list[dict]:
    """List the top N videos from a YouTube channel/playlist URL.

    Returns list of dicts with keys: id, title, url.
    sync_from_date: optional date — passed as yt-dlp `dateafter` to skip older videos.
    """
    if not is_youtube_url(channel_url):
        raise ValueError(f"Not a valid YouTube URL: {channel_url}")

    entries = list_entries(
        channel_url,
        n,
        dateafter=sync_from_date.strftime("%Y%m%d") if sync_from_date is not None else None,
    )

    results = []
    for entry in entries:
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
    if not is_youtube_url(video_url_):
        raise ValueError(f"Not a valid YouTube URL: {video_url_}")
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
    """Download a single video and return ``(file_path, description, transcript)``.

    The description is yt-dlp's `description` field (falls back to ""). The
    transcript is flattened plain text from subtitles/auto-captions already
    present in the extracted info (falls back to "" if none exist). The
    pipeline worker forwards both to the LLM rewrite step so we don't pay a
    second metadata request.

    If max_duration_seconds is set:
    - Videos longer than 3x max_duration_seconds are skipped (VideoTooLong).
    - Downloaded content is trimmed to 0..max_duration_seconds.

    Raises:
        VideoTooLong: if video duration exceeds 3 * max_duration_seconds.
        DownloadError: if yt-dlp fails for any other reason.
    """
    if not is_youtube_url(video_url_):
        raise ValueError(f"Not a valid YouTube URL: {video_url_}")

    file_path, info = download_video(video_url_, output_dir, max_duration_seconds)
    return file_path, str(info.get("description") or ""), fetch_transcript(info)
