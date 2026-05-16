"""YouTube channel listing and video download via yt-dlp Python API.

Does NOT use subprocess. All interaction is through yt_dlp.YoutubeDL.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yt_dlp

from api.services.youtube_validate import is_youtube_url


class VideoTooLong(Exception):
    """Raised when a video exceeds the maximum duration filter."""


class DownloadError(Exception):
    """Raised when yt-dlp fails to download a video."""


def list_top_n(channel_url: str, n: int = 5) -> list[dict]:
    """List the top N videos from a YouTube channel/playlist URL.

    Returns list of dicts with keys: id, title, url.
    """
    if not is_youtube_url(channel_url):
        raise ValueError(f"Not a valid YouTube URL: {channel_url}")

    opts = {
        "extract_flat": "in_playlist",
        "playlistend": n,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if info is None:
        return []

    entries = info.get("entries") or []
    results = []
    for entry in entries:
        if entry is None:
            continue
        video_id = entry.get("id", "")
        title = entry.get("title", "")
        url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        results.append({"id": video_id, "title": title, "url": url})
        if len(results) >= n:
            break

    return results


def download(
    video_url: str,
    output_dir: str,
    max_duration_seconds: Optional[int] = None,
) -> str:
    """Download a single video and return the path to the downloaded file.

    If max_duration_seconds is set:
    - Videos longer than 3x max_duration_seconds are skipped (VideoTooLong).
    - Downloaded content is trimmed to 0..max_duration_seconds via --download-sections.

    Raises:
        VideoTooLong: if video duration exceeds 3 * max_duration_seconds.
        DownloadError: if yt-dlp fails for any other reason.
    """
    if not is_youtube_url(video_url):
        raise ValueError(f"Not a valid YouTube URL: {video_url}")

    os.makedirs(output_dir, exist_ok=True)
    outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")

    opts: dict = {
        "outtmpl": outtmpl,
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "max_filesize": 500 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
    }

    if max_duration_seconds is not None:
        limit = 3 * max_duration_seconds
        opts["match_filter"] = yt_dlp.utils.match_filter_func(f"duration <= {limit}")
        opts["download_ranges"] = yt_dlp.utils.download_range_func(
            None, [(0, max_duration_seconds)]
        )
        opts["force_keyframes_at_cuts"] = True

    downloaded_files: list[str] = []

    class _PP(yt_dlp.postprocessor.PostProcessor):
        def run(self, info):
            path = info.get("filepath") or info.get("filename", "")
            if path:
                downloaded_files.append(path)
            return [], info

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.add_post_processor(_PP(), when="after_move")
        try:
            result = ydl.extract_info(video_url, download=True)
        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e)
            if "match_filter" in err_msg or "Skipping" in err_msg:
                raise VideoTooLong(
                    f"Video duration exceeds {3 * max_duration_seconds}s limit"
                ) from e
            raise DownloadError(err_msg) from e

    if result is None:
        raise DownloadError("yt-dlp returned no info (video unavailable?)")

    if downloaded_files:
        return downloaded_files[-1]

    video_id = result.get("id", "unknown")
    for ext in ("mp4", "webm", "mkv"):
        candidate = os.path.join(output_dir, f"{video_id}.{ext}")
        if os.path.exists(candidate):
            return candidate

    raise DownloadError(f"Download completed but output file not found for {video_url}")
