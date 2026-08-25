"""Download a single video from a pasted URL for manual re-upload.

Separate from `youtube_channel` because that module is channel-oriented and
refuses non-YouTube URLs. Here the operator pastes one-off links from any
supported host and uploads the result to TikTok by hand.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

from api.services.ytdlp_opts import extractor_args

log = logging.getLogger(__name__)

# How long a downloaded file is kept before the retention purge removes it.
RETENTION = timedelta(hours=24)

MAX_FILESIZE_BYTES = 500 * 1024 * 1024

# Host suffix → source_type. yt-dlp has an extractor for each of these.
_HOST_SOURCES: tuple[tuple[str, str], ...] = (
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("tiktok.com", "tiktok"),
    ("douyin.com", "douyin"),
    ("iesdouyin.com", "douyin"),
)

SUPPORTED_SOURCES = tuple(dict.fromkeys(src for _, src in _HOST_SOURCES))


class UnsupportedUrl(ValueError):
    """The URL's host has no supported extractor."""


class DownloadFailed(Exception):
    """yt-dlp could not produce a file for this URL."""


@dataclass
class DownloadedVideo:
    file_path: str
    title: str
    description: str
    duration_seconds: Optional[int]
    file_size: int


def detect_source(url: str) -> str:
    """Return the source_type for `url`, or raise UnsupportedUrl."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError as e:
        raise UnsupportedUrl(f"invalid URL: {url}") from e
    if not host:
        raise UnsupportedUrl(f"invalid URL: {url}")
    for suffix, source in _HOST_SOURCES:
        if host == suffix or host.endswith("." + suffix):
            return source
    raise UnsupportedUrl(f"unsupported host: {host}")


def storage_dir() -> str:
    """Directory holding manually downloaded files, created on first use."""
    path = os.environ.get("MANUAL_DOWNLOAD_DIR") or os.path.join(
        os.getcwd(), "manual_downloads"
    )
    os.makedirs(path, exist_ok=True)
    return path


def download(url: str, output_dir: str) -> DownloadedVideo:
    """Download `url` into `output_dir` and return the resulting file's metadata.

    Raises:
        UnsupportedUrl: host has no supported extractor.
        DownloadFailed: yt-dlp failed or produced no file.
    """
    detect_source(url)
    os.makedirs(output_dir, exist_ok=True)

    opts: dict = {
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILESIZE_BYTES,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
        "noplaylist": True,
        **extractor_args(),
    }

    produced: list[str] = []

    class _CapturePath(yt_dlp.postprocessor.PostProcessor):
        def run(self, info):
            path = info.get("filepath") or info.get("filename", "")
            if path:
                produced.append(path)
            return [], info

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.add_post_processor(_CapturePath(), when="after_move")
        try:
            info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadFailed(str(e)) from e

    if info is None:
        raise DownloadFailed("yt-dlp returned no info (video unavailable?)")

    file_path = produced[-1] if produced else ""
    if not file_path or not os.path.exists(file_path):
        video_id = info.get("id", "unknown")
        for ext in ("mp4", "webm", "mkv"):
            candidate = os.path.join(output_dir, f"{video_id}.{ext}")
            if os.path.exists(candidate):
                file_path = candidate
                break
    if not file_path or not os.path.exists(file_path):
        raise DownloadFailed(f"download completed but no output file for {url}")

    duration = info.get("duration")
    return DownloadedVideo(
        file_path=file_path,
        title=str(info.get("title") or ""),
        description=str(info.get("description") or ""),
        duration_seconds=int(duration) if isinstance(duration, (int, float)) else None,
        file_size=os.path.getsize(file_path),
    )


def row_dir(download_id: int) -> str:
    """Own directory per download so cleanup is one rmtree and names can't collide."""
    return os.path.join(storage_dir(), str(download_id))


def remove_dir(path: Optional[str]) -> None:
    """Delete a download's directory, best effort."""
    if not path:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception as e:  # pragma: no cover - rmtree already swallows most
        log.warning("Failed to remove %s: %s", path, e)


def signed_path(download_id: int, token: str) -> str:
    """Path of the no-auth download endpoint (see nginx `location /api/public/`)."""
    return f"/api/public/downloads/{download_id}/{token}"


def sign_id(download_id: int) -> str:
    from api.services import secrets

    return secrets.sign(f"download:{download_id}")


def verify_id(download_id: int, token: str) -> bool:
    from api.services import secrets

    return secrets.verify(f"download:{download_id}", token)


@dataclass
class PublishedVideo:
    """Where a published file ended up, and how to reach it."""

    url: str
    # The file MOVED here. Callers holding the old path must use this one.
    file_path: str


def publish(
    *,
    file_path: str,
    url: str,
    source_type: str,
    title: Optional[str],
    pipeline_id: Optional[int],
) -> Optional[PublishedVideo]:
    """Move a downloaded file into the retained store and return its link.

    Returns None when no public base URL is configured, when the move fails, or
    when the file is already gone — the caller falls back to a text-only
    notification rather than promising a link that 404s.
    """
    from sqlmodel import Session

    import api.db as _db
    from api.db import now_utc
    from api.models import ManualDownload
    from api.services.public_url import public_base_url

    base = public_base_url()
    if not base:
        log.warning(
            "Cannot publish %s: set PUBLIC_BASE_URL (or HOST) to the address "
            "this deployment is reachable at", file_path,
        )
        return None
    if not os.path.exists(file_path):
        log.warning("Cannot publish %s: file is gone", file_path)
        return None

    row = ManualDownload(
        url=url,
        source_type=source_type,
        origin="pipeline",
        pipeline_id=pipeline_id,
        status="ready",
        title=(title or None) and title[:500],
        expires_at=now_utc() + RETENTION,
    )
    with Session(_db.engine) as s:
        s.add(row)
        s.commit()
        s.refresh(row)
        download_id = row.id

    target_dir = row_dir(download_id)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(file_path))
    try:
        shutil.move(file_path, target)
    except OSError as e:
        log.warning("Failed to move %s into the download store: %s", file_path, e)
        remove_dir(target_dir)
        with Session(_db.engine) as s:
            stale = s.get(ManualDownload, download_id)
            if stale is not None:
                s.delete(stale)
                s.commit()
        return None

    with Session(_db.engine) as s:
        stored = s.get(ManualDownload, download_id)
        if stored is not None:
            stored.file_path = target
            stored.file_size = os.path.getsize(target)
            s.add(stored)
            s.commit()

    return PublishedVideo(
        url=base + signed_path(download_id, sign_id(download_id)),
        file_path=target,
    )


def purge_expired() -> int:
    """Delete expired downloads and their files. Safe to call repeatedly.

    Called from the API startup and from the scheduler's daily retention pass,
    so a long-running API still frees disk without a restart.
    """
    # Imported here: the API models import this module's siblings, and a
    # module-level import would make that cycle real.
    from sqlalchemy import delete as sa_delete
    from sqlmodel import Session, select

    import api.db as _db
    from api.db import now_utc
    from api.models import ManualDownload

    with Session(_db.engine) as s:
        expired = s.exec(
            select(ManualDownload).where(ManualDownload.expires_at < now_utc())
        ).all()
        ids = [r.id for r in expired]
        for row in expired:
            remove_dir(row_dir(row.id))
        if ids:
            s.exec(sa_delete(ManualDownload).where(ManualDownload.id.in_(ids)))
            s.commit()
    if ids:
        log.info("purge_expired: removed %d manual download(s)", len(ids))
    return len(ids)
