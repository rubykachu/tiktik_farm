"""The yt-dlp half of a channel source, with no platform knowledge.

`api.services.youtube_channel` and `api.services.tiktok_channel` are both a
thin wrapper around this: they add the things that really are per-platform —
which hosts the URL may point at, how a bare video id becomes a URL, and
whether the extractor yields subtitles worth reading. Everything below is
identical for either platform because both go through `yt_dlp.YoutubeDL`
against a channel page and then a video page.

Does NOT use subprocess. All interaction is through yt_dlp.YoutubeDL.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from api.services.ytdlp_opts import extractor_args

log = logging.getLogger(__name__)

MAX_FILESIZE_BYTES = 500 * 1024 * 1024
# yt-dlp waits on a socket forever by default. A listing that never answers
# then hangs the whole caller: a discovery scan froze at 11 of 13 channels for
# over eight minutes, its progress stuck and its run never finalising, because
# one channel's request never came back. A listing is a handful of small
# requests, so a minute is already generous.
SOCKET_TIMEOUT_SECONDS = 60
# A video longer than this multiple of the pipeline's max_duration_seconds is
# skipped outright rather than trimmed: trimming an hour-long upload down to a
# minute produces something unrelated to what the channel actually posted.
TOO_LONG_MULTIPLE = 3


class VideoTooLong(Exception):
    """Raised when a video exceeds the maximum duration filter."""


class DownloadError(Exception):
    """Raised when yt-dlp fails to download a video."""


# Browser TLS fingerprints to impersonate, best first. TikTok answers a
# request carrying an ordinary Python TLS fingerprint with a stripped page
# and no error, which surfaces as an empty listing — measured on the same
# host and public IP within one minute: plain TLS returned 0 entries in
# 0.3s from the Debian container while macOS's own Python returned 12.
# Impersonating fixes it. Both Safari and Chrome work for the feed endpoint;
# only Safari also works for a profile URL, so it goes first.
_IMPERSONATE_PREFERENCE = (ImpersonateTarget("safari"), ImpersonateTarget("chrome"))


def _impersonate_target() -> Optional[ImpersonateTarget]:
    """The first preferred fingerprint this build can actually produce, or
    None when curl_cffi is missing — passing a target nothing supports makes
    yt-dlp fail the request outright, which is worse than plain TLS."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            available = [target for target, _ in ydl._get_available_impersonate_targets()]
    except Exception:
        log.warning("could not list yt-dlp impersonate targets", exc_info=True)
        return None
    for preferred in _IMPERSONATE_PREFERENCE:
        if any(preferred in candidate for candidate in available):
            return preferred
    return None


def impersonating_opts() -> dict:
    """yt-dlp options that make a request look like a browser at the TLS
    level. Empty when this build cannot impersonate, so callers degrade to
    plain TLS rather than erroring."""
    target = _impersonate_target()
    return {"impersonate": target} if target else {}


def list_entries(
    channel_url: str,
    n: int,
    dateafter: Optional[str] = None,
    impersonate: bool = False,
) -> list[dict]:
    """Flat listing of a channel/playlist URL, newest first, at most `n`
    entries. `dateafter` is yt-dlp's own `YYYYMMDD` filter; a source whose
    extractor ignores it has to filter the returned entries itself.
    `impersonate` is required by TikTok — see impersonating_opts."""
    opts = {
        "extract_flat": "in_playlist",
        "playlistend": n,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        **extractor_args(),
        **(impersonating_opts() if impersonate else {}),
    }
    if dateafter is not None:
        opts["dateafter"] = dateafter

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if info is None:
        return []
    return [e for e in (info.get("entries") or []) if e]


def extract_info(video_url: str) -> dict:
    """Metadata for one video, without downloading it."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": SOCKET_TIMEOUT_SECONDS,
        **extractor_args(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    return info or {}


def download_video(
    video_url: str,
    output_dir: str,
    max_duration_seconds: Optional[int] = None,
    impersonate: bool = False,
) -> tuple[str, dict]:
    """Download one video into `output_dir`, returning ``(file_path, info)``.

    The caller reads whatever it needs off `info` — description, subtitles —
    so a download never costs a second metadata request.

    If max_duration_seconds is set:
    - Videos longer than TOO_LONG_MULTIPLE * max_duration_seconds are skipped
      (VideoTooLong).
    - Downloaded content is trimmed to 0..max_duration_seconds.

    Raises:
        VideoTooLong: video duration exceeds the skip threshold.
        DownloadError: yt-dlp failed, or produced no file.
    """
    os.makedirs(output_dir, exist_ok=True)

    opts: dict = {
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILESIZE_BYTES,
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        **extractor_args(),
        **(impersonating_opts() if impersonate else {}),
    }

    if max_duration_seconds is not None:
        limit = TOO_LONG_MULTIPLE * max_duration_seconds
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
                    f"Video duration exceeds {TOO_LONG_MULTIPLE * max_duration_seconds}s limit"
                ) from e
            raise DownloadError(err_msg) from e

    if result is None:
        raise DownloadError("yt-dlp returned no info (video unavailable?)")

    if downloaded_files:
        return downloaded_files[-1], result

    # The post-processor does not fire for every format path, so fall back to
    # the name the output template would have produced.
    video_id = result.get("id", "unknown")
    for ext in ("mp4", "webm", "mkv"):
        candidate = os.path.join(output_dir, f"{video_id}.{ext}")
        if os.path.exists(candidate):
            return candidate, result

    raise DownloadError(f"Download completed but output file not found for {video_url}")
