"""Download videos from a Douyin creator profile via douyin-downloader vendor.

Uses subprocess to call vendor/douyin-downloader/run.py with a temp config YAML.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_VENDOR_RUN = os.path.join(
    os.path.dirname(__file__), "..", "..", "vendor", "douyin-downloader", "run.py"
)


class DouyinDownloadError(Exception):
    pass


class DouyinVideoTooLong(Exception):
    pass


@dataclass
class DownloadedVideo:
    aweme_id: str
    file_path: str
    title: str
    description: str
    duration_seconds: int


def _build_config(
    creator_url: str,
    fetch_count: int,
    cookie_dict: dict,
    output_dir: str,
    db_path: str,
) -> dict:
    return {
        "link": [creator_url],
        "path": output_dir + "/",
        "mode": ["post"],
        "number": {"post": fetch_count},
        "increase": {"post": True},
        "thread": 3,
        "retry_times": 3,
        "database": True,
        "database_path": db_path,
        "music": False,
        "cover": False,
        "avatar": False,
        "json": True,
        "folderstyle": False,
        "browser_fallback": {"enabled": False},
        "progress": {"quiet_logs": True},
        "cookies": {
            "msToken": cookie_dict.get("msToken", ""),
            "ttwid": cookie_dict.get("ttwid", ""),
            "odin_tt": cookie_dict.get("odin_tt", ""),
            "passport_csrf_token": cookie_dict.get("passport_csrf_token", ""),
            "sid_guard": cookie_dict.get("sid_guard", ""),
        },
    }


def _scan_output_dir(output_dir: str) -> list[DownloadedVideo]:
    """Scan output_dir for *_data.json files and return DownloadedVideo for each
    that has a matching .mp4 file."""
    videos = []
    for data_path in glob.glob(os.path.join(output_dir, "*_data.json")):
        filename = os.path.basename(data_path)
        aweme_id = filename[: -len("_data.json")]
        mp4_path = os.path.join(output_dir, f"{aweme_id}.mp4")
        if not os.path.exists(mp4_path):
            log.debug("Skipping %s — no .mp4 found (image-note?)", aweme_id)
            continue
        try:
            with open(data_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Cannot read %s: %s", data_path, e)
            continue

        duration_ms = data.get("video", {}).get("duration", 0)
        duration_seconds = int(duration_ms) // 1000
        title = data.get("desc", "")
        description = data.get("desc", "")

        videos.append(DownloadedVideo(
            aweme_id=aweme_id,
            file_path=mp4_path,
            title=title,
            description=description,
            duration_seconds=duration_seconds,
        ))
    return videos


def cleanup_aweme_files(output_dir: str, aweme_id: str) -> None:
    """Delete all files belonging to an aweme (mp4, jpg, json, mp3)."""
    for path in glob.glob(os.path.join(output_dir, f"{aweme_id}*")):
        try:
            os.unlink(path)
        except OSError as e:
            log.warning("Failed to delete %s: %s", path, e)


def download_creator_posts(
    creator_url: str,
    fetch_count: int,
    cookie_dict: dict,
    output_dir: str,
    db_path: str,
    max_duration_seconds: Optional[int] = None,
) -> list[DownloadedVideo]:
    """Run douyin-downloader for a creator URL, return list of downloaded videos.

    Image-notes (no .mp4) are silently skipped.
    Videos longer than max_duration_seconds are removed and excluded.
    Temp config YAML is always deleted after subprocess returns.
    """
    os.makedirs(output_dir, exist_ok=True)
    config = _build_config(creator_url, fetch_count, cookie_dict, output_dir, db_path)

    config_fd, config_path = tempfile.mkstemp(suffix=".yml", prefix="douyin_cfg_")
    try:
        with os.fdopen(config_fd, "w") as f:
            yaml.dump(config, f, allow_unicode=True)

        vendor_run = os.path.abspath(_VENDOR_RUN)
        try:
            result = subprocess.run(
                [sys.executable, vendor_run, "-c", config_path],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise DouyinDownloadError("douyin-downloader timed out after 600 s")
        if result.returncode != 0:
            log.warning("douyin-downloader exited %d: %s", result.returncode, result.stderr[:500])
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass

    videos = _scan_output_dir(output_dir)

    if max_duration_seconds is not None:
        filtered = []
        for v in videos:
            if v.duration_seconds > max_duration_seconds:
                log.info(
                    "Skipping aweme %s — duration %ds > max %ds",
                    v.aweme_id, v.duration_seconds, max_duration_seconds,
                )
                cleanup_aweme_files(output_dir, v.aweme_id)
            else:
                filtered.append(v)
        return filtered

    return videos
