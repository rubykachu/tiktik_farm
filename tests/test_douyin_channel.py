"""Unit tests for api/services/douyin_channel.py

All subprocess and filesystem I/O is mocked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


COOKIE_DICT = {
    "ttwid": "abc123",
    "msToken": "tok456",
    "odin_tt": "odin789",
    "passport_csrf_token": "csrf000",
    "sid_guard": "sid111",
}


def _write_aweme_files(output_dir: str, aweme_id: str, duration_ms: int, with_mp4: bool = True):
    """Write fake _data.json (and optionally .mp4) for an aweme in output_dir."""
    data = {
        "aweme_id": aweme_id,
        "desc": f"title of {aweme_id}",
        "video": {"duration": duration_ms},
    }
    Path(output_dir, f"{aweme_id}_data.json").write_text(json.dumps(data))
    if with_mp4:
        Path(output_dir, f"{aweme_id}.mp4").write_bytes(b"fake_mp4")


class TestDownloadCreatorPosts:
    def test_returns_downloaded_videos(self, tmp_path):
        from api.services.douyin_channel import download_creator_posts, DownloadedVideo

        def fake_run(*args, **kwargs):
            # Simulate douyin-downloader writing files
            _write_aweme_files(str(tmp_path), "111111111111111111", 30_000)
            return MagicMock(returncode=0)

        with patch("api.services.douyin_channel.subprocess.run", side_effect=fake_run):
            result = download_creator_posts(
                creator_url="https://www.douyin.com/user/MS4wLjAB",
                fetch_count=3,
                cookie_dict=COOKIE_DICT,
                output_dir=str(tmp_path),
                db_path=str(tmp_path / "douyin_dl.db"),
            )

        assert len(result) == 1
        assert isinstance(result[0], DownloadedVideo)
        assert result[0].aweme_id == "111111111111111111"
        assert result[0].duration_seconds == 30
        assert result[0].file_path.endswith(".mp4")

    def test_image_note_skipped(self, tmp_path):
        """Aweme with _data.json but no .mp4 is skipped (image-note)."""
        from api.services.douyin_channel import download_creator_posts

        def fake_run(*args, **kwargs):
            _write_aweme_files(str(tmp_path), "222222222222222222", 20_000, with_mp4=False)
            return MagicMock(returncode=0)

        with patch("api.services.douyin_channel.subprocess.run", side_effect=fake_run):
            result = download_creator_posts(
                creator_url="https://www.douyin.com/user/MS4wLjAB",
                fetch_count=3,
                cookie_dict=COOKIE_DICT,
                output_dir=str(tmp_path),
                db_path=str(tmp_path / "douyin_dl.db"),
            )

        assert result == []

    def test_max_duration_filter(self, tmp_path):
        """Videos longer than max_duration_seconds are filtered and their files deleted."""
        from api.services.douyin_channel import download_creator_posts

        def fake_run(*args, **kwargs):
            _write_aweme_files(str(tmp_path), "333333333333333333", 120_000)  # 120s
            _write_aweme_files(str(tmp_path), "444444444444444444", 30_000)   # 30s
            return MagicMock(returncode=0)

        with patch("api.services.douyin_channel.subprocess.run", side_effect=fake_run):
            result = download_creator_posts(
                creator_url="https://www.douyin.com/user/MS4wLjAB",
                fetch_count=3,
                cookie_dict=COOKIE_DICT,
                output_dir=str(tmp_path),
                db_path=str(tmp_path / "douyin_dl.db"),
                max_duration_seconds=60,
            )

        assert len(result) == 1
        assert result[0].aweme_id == "444444444444444444"
        # Files for the long video should be cleaned up
        assert not Path(tmp_path, "333333333333333333.mp4").exists()

    def test_config_yaml_deleted_after_run(self, tmp_path):
        """Temp config YAML must not persist after subprocess returns."""
        from api.services.douyin_channel import download_creator_posts

        written_configs: list[str] = []

        def fake_run(cmd, *args, **kwargs):
            # cmd = ["python", "...", "-c", "<config_path>"]
            cfg_path = cmd[cmd.index("-c") + 1]
            written_configs.append(cfg_path)
            return MagicMock(returncode=0)

        with patch("api.services.douyin_channel.subprocess.run", side_effect=fake_run):
            download_creator_posts(
                creator_url="https://www.douyin.com/user/MS4wLjAB",
                fetch_count=1,
                cookie_dict=COOKIE_DICT,
                output_dir=str(tmp_path),
                db_path=str(tmp_path / "douyin_dl.db"),
            )

        assert len(written_configs) == 1
        assert not os.path.exists(written_configs[0])


class TestCleanupAwemeFiles:
    def test_deletes_all_aweme_files(self, tmp_path):
        from api.services.douyin_channel import cleanup_aweme_files

        aweme_id = "555555555555555555"
        for ext in ("mp4", "jpg", "_data.json", ".mp3"):
            Path(tmp_path, f"{aweme_id}{ext}").write_bytes(b"x")
        # Add an unrelated file that must NOT be deleted
        Path(tmp_path, "other_file.mp4").write_bytes(b"x")

        cleanup_aweme_files(str(tmp_path), aweme_id)

        assert not Path(tmp_path, f"{aweme_id}.mp4").exists()
        assert not Path(tmp_path, f"{aweme_id}.jpg").exists()
        assert not Path(tmp_path, f"{aweme_id}_data.json").exists()
        assert Path(tmp_path, "other_file.mp4").exists()
