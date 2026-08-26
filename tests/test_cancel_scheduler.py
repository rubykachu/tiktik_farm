"""Tests for the scheduler-side half of cancel-download: the active-download
registry, the psutil-based ffmpeg killer, and the poll-loop hook that acts on
ChannelPipeline.cancel_requested_at."""
import pytest
from unittest.mock import MagicMock, patch

import psutil
from sqlmodel import Session, SQLModel

import api.db as _api_db
from api.db import now_utc
from api.models import ChannelPipeline
from scheduler.pipeline_worker import (
    _active_downloads,
    _clear_active_download,
    _kill_ffmpeg_under,
    _register_active_download,
    check_cancel_requests,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    from api.db import make_engine, override_engine

    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))


@pytest.fixture(autouse=True)
def clean_registry():
    _active_downloads.clear()
    yield
    _active_downloads.clear()


def _make_pipeline(**overrides) -> int:
    now = now_utc()
    defaults = dict(
        name="test",
        channel_url="https://www.youtube.com/@test/videos",
        source_type="youtube",
        fetch_count=3,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now,
        next_fire_at=now,
        state="active",
        caption_template="{title}",
        upload_options_json="{}",
        account_ids_json="[1]",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    with Session(_api_db.engine) as s:
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


def _fake_proc(name: str, cmdline: list[str]) -> MagicMock:
    proc = MagicMock()
    proc.info = {"name": name, "cmdline": cmdline}
    return proc


class TestActiveDownloadsRegistry:
    def test_register_and_clear(self):
        _register_active_download(1, "/tmp/a")
        assert _active_downloads[1] == "/tmp/a"
        _clear_active_download(1)
        assert 1 not in _active_downloads

    def test_clear_missing_pipeline_is_noop(self):
        _clear_active_download(999)  # must not raise


class TestKillFfmpegUnder:
    def test_kills_matching_process_only(self):
        target = _fake_proc("ffmpeg", ["ffmpeg", "-i", "file:/data/pipeline_tmp/abc/x.mp4.part"])
        other_dir = _fake_proc("ffmpeg", ["ffmpeg", "-i", "file:/data/pipeline_tmp/xyz/y.mp4.part"])
        other_name = _fake_proc("python", ["python", "-m", "scheduler.main"])

        with patch(
            "scheduler.pipeline_worker.psutil.process_iter",
            return_value=[target, other_dir, other_name],
        ):
            killed = _kill_ffmpeg_under("/data/pipeline_tmp/abc")

        assert killed == 1
        target.kill.assert_called_once()
        other_dir.kill.assert_not_called()
        other_name.kill.assert_not_called()

    def test_no_match_returns_zero(self):
        proc = _fake_proc("ffmpeg", ["ffmpeg", "-i", "file:/data/pipeline_tmp/other/x.mp4.part"])
        with patch("scheduler.pipeline_worker.psutil.process_iter", return_value=[proc]):
            killed = _kill_ffmpeg_under("/data/pipeline_tmp/abc")
        assert killed == 0

    def test_dead_process_between_scan_and_kill_is_swallowed(self):
        proc = _fake_proc("ffmpeg", ["ffmpeg", "-i", "file:/data/pipeline_tmp/abc/x.mp4.part"])
        proc.kill.side_effect = psutil.NoSuchProcess(123)
        with patch("scheduler.pipeline_worker.psutil.process_iter", return_value=[proc]):
            killed = _kill_ffmpeg_under("/data/pipeline_tmp/abc")
        assert killed == 0


class TestCheckCancelRequests:
    def test_kills_active_download_and_clears_flag(self):
        pid = _make_pipeline(cancel_requested_at=now_utc())
        _register_active_download(pid, "/data/pipeline_tmp/xyz")

        with patch(
            "scheduler.pipeline_worker._kill_ffmpeg_under", return_value=1
        ) as mock_kill:
            check_cancel_requests()

        mock_kill.assert_called_once_with("/data/pipeline_tmp/xyz")
        with Session(_api_db.engine) as s:
            p = s.get(ChannelPipeline, pid)
            assert p.cancel_requested_at is None

    def test_clears_flag_when_no_active_download(self):
        pid = _make_pipeline(cancel_requested_at=now_utc())

        with patch("scheduler.pipeline_worker._kill_ffmpeg_under") as mock_kill:
            check_cancel_requests()

        mock_kill.assert_not_called()
        with Session(_api_db.engine) as s:
            p = s.get(ChannelPipeline, pid)
            assert p.cancel_requested_at is None

    def test_noop_when_nothing_pending(self):
        _make_pipeline()  # no cancel_requested_at
        with patch("scheduler.pipeline_worker._kill_ffmpeg_under") as mock_kill:
            check_cancel_requests()
        mock_kill.assert_not_called()
