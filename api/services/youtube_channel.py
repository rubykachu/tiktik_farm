"""YouTube channel listing and video download via yt-dlp Python API.

Does NOT use subprocess. All interaction is through yt_dlp.YoutubeDL.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Optional

import requests
import yt_dlp

from api.services.ytdlp_opts import extractor_args

from api.services.youtube_validate import is_youtube_url

log = logging.getLogger(__name__)

_TRANSCRIPT_LANG_PREFERENCE = ("en",)
_TRANSCRIPT_FETCH_TIMEOUT = 10  # seconds


class VideoTooLong(Exception):
    """Raised when a video exceeds the maximum duration filter."""


class DownloadError(Exception):
    """Raised when yt-dlp fails to download a video."""


def list_top_n(channel_url: str, n: int = 5, sync_from_date: Optional[date] = None) -> list[dict]:
    """List the top N videos from a YouTube channel/playlist URL.

    Returns list of dicts with keys: id, title, url.
    sync_from_date: optional date — passed as yt-dlp `dateafter` to skip older videos.
    """
    if not is_youtube_url(channel_url):
        raise ValueError(f"Not a valid YouTube URL: {channel_url}")

    opts = {
        "extract_flat": "in_playlist",
        "playlistend": n,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        **extractor_args(),
    }
    if sync_from_date is not None:
        opts["dateafter"] = sync_from_date.strftime("%Y%m%d")

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


def get_video_info(video_url: str) -> dict:
    """Fetch title and description without downloading. Returns {title, description}."""
    if not is_youtube_url(video_url):
        raise ValueError(f"Not a valid YouTube URL: {video_url}")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        **extractor_args(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    if not info:
        raise ValueError("Could not fetch video info")
    return {
        "title": info.get("title", ""),
        "description": info.get("description", ""),
    }


def _pick_caption_track(captions: dict) -> Optional[list[dict]]:
    """Pick one language's list of caption format entries, preferring English."""
    for lang in _TRANSCRIPT_LANG_PREFERENCE:
        if captions.get(lang):
            return captions[lang]
    for lang in sorted(captions):
        if captions[lang]:
            return captions[lang]
    return None


def _pick_vtt_entry(entries: list[dict]) -> Optional[dict]:
    for entry in entries:
        if entry.get("ext") == "vtt":
            return entry
    return entries[0] if entries else None


def _vtt_to_text(vtt: str) -> str:
    """Flatten a WebVTT caption file to plain text, deduping the repeated
    lines that rolling auto-captions produce."""
    lines = []
    last = None
    for raw_line in vtt.splitlines():
        line = raw_line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        if line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not text or text == last:
            continue
        last = text
        lines.append(text)
    return " ".join(lines)


def _fetch_transcript(info: dict) -> str:
    """Best-effort transcript from yt-dlp's already-fetched caption metadata.

    Prefers uploader-provided subtitles over auto-generated ones. Returns ""
    on any failure — a transcript is a nice-to-have for the LLM rewrite, not
    something worth failing the download over.
    """
    track = _pick_caption_track(info.get("subtitles") or {})
    if track is None:
        track = _pick_caption_track(info.get("automatic_captions") or {})
    if track is None:
        return ""
    entry = _pick_vtt_entry(track)
    if not entry or not entry.get("url"):
        return ""
    try:
        resp = requests.get(entry["url"], timeout=_TRANSCRIPT_FETCH_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Failed to fetch transcript: %s", e)
        return ""
    return _vtt_to_text(resp.text)


def download(
    video_url: str,
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
        **extractor_args(),
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

    description = str(result.get("description") or "")
    transcript = _fetch_transcript(result)

    if downloaded_files:
        return downloaded_files[-1], description, transcript

    video_id = result.get("id", "unknown")
    for ext in ("mp4", "webm", "mkv"):
        candidate = os.path.join(output_dir, f"{video_id}.{ext}")
        if os.path.exists(candidate):
            return candidate, description, transcript

    raise DownloadError(f"Download completed but output file not found for {video_url}")
