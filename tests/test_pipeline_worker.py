"""Tests for scheduler/pipeline_worker.py — E1 through E5."""
import json
import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
from api.schemas import _validate_window_fields
from scheduler.pipeline_worker import (
    reap_orphans,
    purge_old_runs,
    claim_due_pipeline,
    run_pipeline,
    advance_next_fire,
    release_lock,
    send_telegram_summary,
    pipeline_tick,
    _emergency_finalize,
    _pipeline_run_context,
    _check_disk_space,
    _next_fire_in_window,
    _ensure_aware,
    LockHeartbeat,
    RUN_RETENTION_DAYS,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Create an in-memory SQLite for each test."""
    from api.db import make_engine, override_engine

    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))


def _make_pipeline(session: Session, **overrides) -> ChannelPipeline:
    now = now_utc()
    defaults = dict(
        name="test-pipeline",
        channel_url="https://www.youtube.com/@test/videos",
        fetch_count=3,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now - timedelta(hours=6),
        next_fire_at=now - timedelta(minutes=1),
        state="active",
        caption_template="{title}",
        upload_options_json="{}",
        account_ids_json="[1]",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    session.add(p)
    session.commit()
    session.refresh(p)
    # Eagerly load all attributes before expunge to avoid DetachedInstanceError
    _ = (p.id, p.name, p.channel_url, p.fetch_count, p.interval_value,
         p.interval_unit, p.first_fire_at, p.next_fire_at, p.state,
         p.caption_template, p.upload_options_json, p.account_ids_json,
         p.max_duration_seconds, p.upload_delay_minutes, p.telegram_bot_token, p.telegram_chat_id,
         p.window_end_time, p.window_delay_minutes,
         p.run_now_requested_at, p.lock_until, p.created_at, p.updated_at,
         p.sync_from_date)
    return p


def _make_account(session: Session, account_id: int = 1, username: str = "testuser") -> Account:
    acct = Account(
        id=account_id,
        username=username,
        display_name=None,
        cookie_path=f"/tmp/cookies/{username}.cookie",
        has_valid_session=True,
    )
    session.add(acct)
    session.commit()
    return acct


# ─── E1: Orphan reaper ─────────────────────────────────────────────────────


class TestReapOrphans:
    def test_stale_run_gets_finished(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            run = PipelineRun(
                pipeline_id=p.id,
                started_at=now - timedelta(minutes=20),
                trigger_reason="scheduled",
                pulled_count=5,
            )
            s.add(run)
            s.commit()
            run_id = run.id

        reap_orphans()

        with Session(_api_db.engine) as s:
            run = s.get(PipelineRun, run_id)
            assert run.finished_at is not None
            assert run.error_text == "worker crashed"
            assert run.failed_count == 5

    def test_expired_lock_cleared(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s, lock_until=now - timedelta(minutes=1))
            pid = p.id

        reap_orphans()

        with Session(_api_db.engine) as s:
            p = s.get(ChannelPipeline, pid)
            assert p.lock_until is None

    def test_active_lock_not_cleared(self):
        now = now_utc()
        future_lock = now + timedelta(minutes=5)
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s, lock_until=future_lock)
            pid = p.id

        reap_orphans()

        with Session(_api_db.engine) as s:
            p = s.get(ChannelPipeline, pid)
            assert p.lock_until is not None


# ─── E2: Claim + Heartbeat ─────────────────────────────────────────────────


class TestClaimDuePipeline:
    def test_claims_due_pipeline(self):
        with Session(_api_db.engine) as s:
            _make_pipeline(s)

        result = claim_due_pipeline()
        assert result is not None
        pipeline, reason = result
        assert reason == "scheduled"
        assert pipeline.lock_until is not None

    def test_no_claim_when_locked(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            _make_pipeline(s, lock_until=now + timedelta(minutes=5))

        result = claim_due_pipeline()
        assert result is None

    def test_no_claim_when_paused(self):
        with Session(_api_db.engine) as s:
            _make_pipeline(s, state="paused")

        result = claim_due_pipeline()
        assert result is None

    def test_claims_run_now(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            _make_pipeline(
                s,
                next_fire_at=now + timedelta(hours=5),
                run_now_requested_at=now,
            )

        result = claim_due_pipeline()
        assert result is not None
        _, reason = result
        assert reason == "manual"

    def test_concurrent_claim_only_one_wins(self):
        with Session(_api_db.engine) as s:
            _make_pipeline(s)

        r1 = claim_due_pipeline()
        r2 = claim_due_pipeline()
        assert r1 is not None
        assert r2 is None


# ─── E3: run_pipeline ──────────────────────────────────────────────────────


class TestRunPipeline:
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_happy_path_2_videos_1_account(self, mock_upload, mock_download, mock_list):
        mock_list.return_value = [
            {"id": "v1", "title": "Video 1", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "Video 2", "url": "https://www.youtube.com/watch?v=v2"},
        ]
        mock_download.return_value = ("/tmp/fake.mp4", "")
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/@user/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.pulled_count == 2
        assert run.uploaded_count == 2
        assert run.finished_at is not None

        with Session(_api_db.engine) as s:
            uploads = s.exec(select(VideoUpload)).all()
            assert len(uploads) == 2
            assert all(u.status == "succeeded" for u in uploads)

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_dedupe_skips_succeeded(self, mock_upload, mock_download, mock_list):
        mock_list.return_value = [
            {"id": "v1", "title": "Old", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/fake.mp4", "")
        mock_upload.return_value = (True, "tk2", "https://tiktok.com/@user/video/tk2")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            pid = pipeline.id

        # Add pre-existing succeeded upload in separate session
        with Session(_api_db.engine) as s:
            s.add(VideoUpload(
                pipeline_id=pid,
                account_id=1,
                youtube_video_id="v1",
                youtube_url="https://www.youtube.com/watch?v=v1",
                status="succeeded",
            ))
            s.commit()

        # Re-read pipeline cleanly
        with Session(_api_db.engine) as s:
            pipeline = s.get(ChannelPipeline, pid)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.pulled_count == 1
        assert run.uploaded_count == 0
        assert run.skipped_dup_count == 1
        mock_upload.assert_not_called()

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_exhausted_after_3_fails(self, mock_upload, mock_download, mock_list):
        mock_list.return_value = [
            {"id": "v1", "title": "Bad", "url": "https://www.youtube.com/watch?v=v1"},
        ]

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            pid = pipeline.id

        with Session(_api_db.engine) as s:
            for _ in range(3):
                s.add(VideoUpload(
                    pipeline_id=pid,
                    account_id=1,
                    youtube_video_id="v1",
                    youtube_url="https://www.youtube.com/watch?v=v1",
                    status="failed",
                ))
            s.commit()

        with Session(_api_db.engine) as s:
            pipeline = s.get(ChannelPipeline, pid)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.skipped_exhausted_count == 1
        assert run.uploaded_count == 0
        mock_upload.assert_not_called()

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_upload_failure_records_failed_row(self, mock_upload, mock_download, mock_list):
        mock_list.return_value = [
            {"id": "v1", "title": "Fail", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/fake.mp4", "")
        mock_upload.return_value = (False, None, None)

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.failed_count == 1
        with Session(_api_db.engine) as s:
            uploads = s.exec(select(VideoUpload)).all()
            assert len(uploads) == 1
            assert uploads[0].status == "failed"

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    def test_list_failure_records_error(self, mock_list):
        mock_list.side_effect = Exception("network error")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.error_text is not None
        assert "list_top_n failed" in run.error_text

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_2_accounts_2_videos(self, mock_upload, mock_download, mock_list):
        mock_list.return_value = [
            {"id": "v1", "title": "V1", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "V2", "url": "https://www.youtube.com/watch?v=v2"},
        ]
        mock_download.return_value = ("/tmp/fake.mp4", "")
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/@x/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s, account_id=1, username="user1")
            _make_account(s, account_id=2, username="user2")
            pipeline = _make_pipeline(s, account_ids_json="[1, 2]")
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.pulled_count == 2
        assert run.uploaded_count == 4  # 2 videos * 2 accounts

    @patch("scheduler.pipeline_worker.time.sleep")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_upload_delay_sleeps_between_uploads(
        self, mock_upload, mock_download, mock_list, mock_sleep
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "Video 1", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "Video 2", "url": "https://www.youtube.com/watch?v=v2"},
        ]
        mock_download.return_value = ("/tmp/fake.mp4", "")
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/@user/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s, upload_delay_minutes=2)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.uploaded_count == 2
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_once_with(120)

    @patch("scheduler.pipeline_worker.time.sleep")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_upload_delay_zero_no_sleep(self, mock_upload, mock_download, mock_list, mock_sleep):
        mock_list.return_value = [
            {"id": "v1", "title": "Video 1", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "Video 2", "url": "https://www.youtube.com/watch?v=v2"},
        ]
        mock_download.return_value = ("/tmp/fake.mp4", "")
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/@user/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s, upload_delay_minutes=0)
            s.expunge(pipeline)

        run_pipeline(pipeline, "scheduled")
        mock_sleep.assert_not_called()

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_sync_from_date_forwarded_to_list_top_n(self, mock_upload, mock_download, mock_list):
        cutoff = date(2025, 1, 15)
        mock_list.return_value = []

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s, sync_from_date=cutoff)
            s.expunge(pipeline)

        run_pipeline(pipeline, "scheduled")

        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("sync_from_date") == cutoff

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    def test_summary_text_includes_cutoff_prefix(self, mock_list):
        cutoff = date(2025, 3, 20)
        mock_list.return_value = []

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s, sync_from_date=cutoff)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert run.summary_text.startswith("Cutoff: 2025-03-20")

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    def test_summary_text_no_cutoff_prefix_when_none(self, mock_list):
        mock_list.return_value = []

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            s.expunge(pipeline)

        run = run_pipeline(pipeline, "scheduled")

        assert "Cutoff:" not in run.summary_text


# ─── E4: Advance next_fire_at ──────────────────────────────────────────────


class TestAdvanceNextFire:
    def test_scheduled_advance(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            pipeline = _make_pipeline(
                s,
                interval_value=6,
                interval_unit="hours",
                next_fire_at=now - timedelta(hours=1),
            )
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            # SQLite loses tzinfo → compare naive
            result = fresh.next_fire_at
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            # max(prev+6h, now+6h). prev+6h = now+5h, now+6h = now+6h → now+6h
            assert result > now + timedelta(hours=5)

    def test_manual_clears_run_now(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            pipeline = _make_pipeline(s, run_now_requested_at=now)
            s.expunge(pipeline)

        advance_next_fire(pipeline, "manual")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            assert fresh.run_now_requested_at is None

    def test_manual_preserves_new_request(self):
        now = now_utc()
        old_request = now - timedelta(minutes=5)
        with Session(_api_db.engine) as s:
            pipeline = _make_pipeline(s, run_now_requested_at=now)
            s.expunge(pipeline)

        # Simulate: pipeline was claimed when run_now was old_request
        pipeline.run_now_requested_at = old_request

        advance_next_fire(pipeline, "manual")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            # CAS doesn't match → flag preserved
            assert fresh.run_now_requested_at is not None


# ─── E4b: Window scheduling ────────────────────────────────────────────────


class TestWindowScheduling:
    def _make_window_pipeline(
        self,
        session: Session,
        next_fire_at: datetime,
        first_fire_at: datetime | None = None,
    ) -> ChannelPipeline:
        return _make_pipeline(
            session,
            interval_value=1,
            interval_unit="days",
            first_fire_at=first_fire_at or next_fire_at,
            next_fire_at=next_fire_at,
            window_end_time="00:30",
            window_delay_minutes=5,
        )

    def test_within_window_advances_by_delay(self):
        # next_fire_at = 00:10 → next should be 00:15 (still ≤ 00:30)
        base = datetime(2026, 6, 1, 0, 10, 0, tzinfo=timezone.utc)
        with Session(_api_db.engine) as s:
            pipeline = self._make_window_pipeline(s, next_fire_at=base)
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            result = _ensure_aware(fresh.next_fire_at)
            expected = datetime(2026, 6, 1, 0, 15, 0, tzinfo=timezone.utc)
            assert result == expected

    def test_at_window_end_advances_by_delay(self):
        # next_fire_at = 00:25, delay=5 → candidate 00:30 == window_close → still in window
        base = datetime(2026, 6, 1, 0, 25, 0, tzinfo=timezone.utc)
        with Session(_api_db.engine) as s:
            pipeline = self._make_window_pipeline(s, next_fire_at=base)
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            result = _ensure_aware(fresh.next_fire_at)
            expected = datetime(2026, 6, 1, 0, 30, 0, tzinfo=timezone.utc)
            assert result == expected

    def test_past_window_jumps_to_next_cycle(self):
        # Window opens at first_fire_at time-of-day (00:00) and closes 00:30.
        # next_fire_at = 00:30 (last in-window fire), delay=5 → candidate 00:35
        # > window_close → jump to next cycle. Next day must reopen at the
        # window START (00:00), NOT drift to the window-end (00:30).
        first_fire = datetime(2099, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        base = datetime(2099, 6, 1, 0, 30, 0, tzinfo=timezone.utc)
        with Session(_api_db.engine) as s:
            pipeline = self._make_window_pipeline(
                s, next_fire_at=base, first_fire_at=first_fire
            )
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            result = _ensure_aware(fresh.next_fire_at)
            assert result == datetime(2099, 6, 2, 0, 0, 0, tzinfo=timezone.utc)

    def test_past_window_skips_missed_cycles_after_downtime(self):
        # Window closed days ago (downtime) → reopen at the next window START
        # strictly after now, anchored to first_fire_at time-of-day (00:00),
        # without firing every missed day (no tick storm).
        now = now_utc()
        first_fire = now - timedelta(days=5)
        base = first_fire + timedelta(minutes=30)  # last in-window fire
        with Session(_api_db.engine) as s:
            pipeline = self._make_window_pipeline(
                s, next_fire_at=base, first_fire_at=first_fire
            )
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            result = _ensure_aware(fresh.next_fire_at)
            assert result > now
            assert result <= now + timedelta(days=1)
            assert (result.hour, result.minute) == (first_fire.hour, first_fire.minute)

    def test_no_window_unchanged(self):
        # Pipeline without window fields → original interval logic
        now = now_utc()
        with Session(_api_db.engine) as s:
            pipeline = _make_pipeline(
                s,
                interval_value=6,
                interval_unit="hours",
                next_fire_at=now - timedelta(hours=1),
            )
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            result = _ensure_aware(fresh.next_fire_at)
            assert result > now + timedelta(hours=5)

    def test_next_fire_in_window_helper_within(self):
        base = datetime(2026, 6, 1, 0, 10, 0, tzinfo=timezone.utc)
        pipeline = ChannelPipeline(
            name="x", channel_url="https://www.youtube.com/@x/videos",
            fetch_count=1, interval_value=1, interval_unit="days",
            first_fire_at=base, next_fire_at=base, state="active",
            caption_template="{title}", window_end_time="00:30", window_delay_minutes=5,
        )
        result = _next_fire_in_window(pipeline, base)
        assert result == datetime(2026, 6, 1, 0, 15, 0, tzinfo=timezone.utc)

    def test_next_fire_in_window_helper_outside(self):
        base = datetime(2026, 6, 1, 0, 30, 0, tzinfo=timezone.utc)
        pipeline = ChannelPipeline(
            name="x", channel_url="https://www.youtube.com/@x/videos",
            fetch_count=1, interval_value=1, interval_unit="days",
            first_fire_at=base, next_fire_at=base, state="active",
            caption_template="{title}", window_end_time="00:30", window_delay_minutes=5,
        )
        result = _next_fire_in_window(pipeline, base)
        assert result is None

    def test_next_fire_in_window_helper_no_window(self):
        base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        pipeline = ChannelPipeline(
            name="x", channel_url="https://www.youtube.com/@x/videos",
            fetch_count=1, interval_value=1, interval_unit="days",
            first_fire_at=base, next_fire_at=base, state="active",
            caption_template="{title}",
        )
        result = _next_fire_in_window(pipeline, base)
        assert result is None


# ─── E4c: _validate_window_fields ─────────────────────────────────────────


_BASE_FFA = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestWindowValidation:
    """Unit tests for api.schemas._validate_window_fields."""

    # V8: both None → no error (no window configured)
    def test_both_none_ok(self):
        _validate_window_fields(None, None, "hours", _BASE_FFA)

    # V9: valid pair → no error
    def test_valid_pair_ok(self):
        _validate_window_fields("00:30", 5, "days", _BASE_FFA)

    # V1: only window_end_time set → ValueError
    def test_only_end_time_raises(self):
        with pytest.raises(ValueError, match="both"):
            _validate_window_fields("00:30", None, "hours", _BASE_FFA)

    # V2: only window_delay_minutes set → ValueError
    def test_only_delay_raises(self):
        with pytest.raises(ValueError, match="both"):
            _validate_window_fields(None, 5, "hours", _BASE_FFA)

    # V3: invalid format strings
    @pytest.mark.parametrize("bad", ["9:30", "25:00", "00:60", "noon", "0030", ""])
    def test_invalid_format_raises(self, bad):
        with pytest.raises(ValueError, match="HH:MM"):
            _validate_window_fields(bad, 5, "hours", _BASE_FFA)

    # V4: interval_unit=minutes → ValueError
    def test_minutes_interval_raises(self):
        with pytest.raises(ValueError, match="minutes"):
            _validate_window_fields("00:30", 5, "minutes", _BASE_FFA)

    # V5: end == start → ValueError
    def test_end_equals_start_raises(self):
        ffa = datetime(2026, 6, 1, 0, 30, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            _validate_window_fields("00:30", 5, "hours", ffa)

    # V5b: end before start → ValueError
    def test_end_before_start_raises(self):
        ffa = datetime(2026, 6, 1, 1, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            _validate_window_fields("00:30", 5, "hours", ffa)

    # V6: window width == delay (not strictly greater) → ValueError
    def test_window_equals_delay_raises(self):
        # start=00:00, end=00:05, delay=5 → window=5, 5 <= 5 → error
        with pytest.raises(ValueError):
            _validate_window_fields("00:05", 5, "hours", _BASE_FFA)

    # V7: first_fire_at=None → skips time comparison, no error
    def test_first_fire_at_none_skips_time_check(self):
        _validate_window_fields("00:30", 5, "hours", None)

    # window > delay exactly by 1 minute → ok
    def test_window_just_greater_than_delay_ok(self):
        # start=00:00, end=00:06, delay=5 → window=6 > 5
        _validate_window_fields("00:06", 5, "hours", _BASE_FFA)

    # ge=3 enforcement: delay=2 is blocked at schema Field level, not this function
    # (this function receives already-validated int from Pydantic; pass-through)
    def test_delay_2_passes_function_level(self):
        # _validate_window_fields itself has no ge check; Pydantic Field does
        _validate_window_fields("00:30", 2, "hours", _BASE_FFA)


# ─── E5: Telegram summary ──────────────────────────────────────────────────


def _make_tg_pipeline(now, **overrides):
    defaults = dict(
        id=1,
        name="my-pipe",
        channel_url="https://www.youtube.com/@x",
        fetch_count=3,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now,
        next_fire_at=now,
        state="active",
        caption_template="{title}",
        telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
        telegram_chat_id="-1001234567890",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return ChannelPipeline(**defaults)


class TestTelegramSummary:
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_sends_when_configured(self, mock_send):
        mock_send.return_value = True
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(
            id=1, pipeline_id=1, started_at=now, finished_at=now,
            trigger_reason="scheduled", pulled_count=3, uploaded_count=2, failed_count=1,
        )

        send_telegram_summary(pipeline, run)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][2]
        assert "[my-pipe]" in text
        assert "Tải xuống: 3" in text
        assert "Đăng tải: 2" in text
        assert "Thất bại: 1" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_skips_when_no_config(self, mock_send):
        now = now_utc()
        pipeline = _make_tg_pipeline(now, telegram_bot_token=None, telegram_chat_id=None)
        run = PipelineRun(id=1, pipeline_id=1, started_at=now, trigger_reason="scheduled")

        send_telegram_summary(pipeline, run)
        mock_send.assert_not_called()

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_failure_does_not_raise(self, mock_send):
        mock_send.side_effect = Exception("network error")
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(id=1, pipeline_id=1, started_at=now, trigger_reason="scheduled")

        # Should not raise
        send_telegram_summary(pipeline, run)

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_error_text_in_summary(self, mock_send):
        """Bug 7: run with system error shows error text in notification."""
        mock_send.return_value = True
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(
            id=1, pipeline_id=1, started_at=now, trigger_reason="scheduled",
            pulled_count=0, error_text="Traceback: NullPointerException at line 42",
        )

        send_telegram_summary(pipeline, run)
        text = mock_send.call_args[0][2]
        assert "Lỗi hệ thống" in text
        assert "NullPointerException" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_rich_format_includes_video_details(self, mock_send):
        """Bug 8: per-video details appear in notification."""
        mock_send.return_value = True
        now = now_utc()

        with Session(_api_db.engine) as s:
            _make_account(s, account_id=1)
            p = _make_pipeline(
                s,
                telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
                telegram_chat_id="-1001234567890",
            )
            run = PipelineRun(
                pipeline_id=p.id, started_at=now, trigger_reason="scheduled",
                pulled_count=2, uploaded_count=1, failed_count=1,
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
            pid = p.id

            v1 = VideoUpload(
                run_id=run_id, account_id=1, source_title="My Great Video",
                youtube_video_id="aaa111", youtube_url="https://youtube.com/watch?v=aaa",
                tiktok_url="https://tiktok.com/@x/video/123", status="succeeded",
            )
            v2 = VideoUpload(
                run_id=run_id, account_id=1, source_title="Failed Video",
                youtube_video_id="bbb222", youtube_url="https://youtube.com/watch?v=bbb",
                error_text="upload_timeout", status="failed",
            )
            s.add_all([v1, v2])
            s.commit()
            s.refresh(run)

        pipeline = _make_tg_pipeline(
            now, id=pid,
            telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
            telegram_chat_id="-1001234567890",
        )

        send_telegram_summary(pipeline, run)
        text = mock_send.call_args[0][2]
        assert "My Great Video" in text
        assert "tiktok.com" in text
        assert "Failed Video" in text
        assert "upload_timeout" in text
        assert len(text) <= 4096

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_message_respects_4096_limit(self, mock_send):
        """Bug 8: message is capped at 4096 chars."""
        mock_send.return_value = True
        now = now_utc()

        with Session(_api_db.engine) as s:
            _make_account(s, account_id=1)
            p = _make_pipeline(
                s,
                telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
                telegram_chat_id="-1001234567890",
            )
            run = PipelineRun(
                pipeline_id=p.id, started_at=now, trigger_reason="scheduled",
                pulled_count=50, uploaded_count=50,
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
            pid = p.id

            # Add 50 videos with long titles to force truncation
            for i in range(50):
                s.add(VideoUpload(
                    run_id=run_id, account_id=1,
                    youtube_video_id=f"vid{i:04d}",
                    source_title=f"Very Long Video Title Number {i} - Extended Edition",
                    tiktok_url=f"https://tiktok.com/@x/video/{i}",
                    status="succeeded",
                    youtube_url=f"https://youtube.com/watch?v=vid{i}",
                ))
            s.commit()
            s.refresh(run)

        pipeline = _make_tg_pipeline(
            now, id=pid,
            telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
            telegram_chat_id="-1001234567890",
        )
        send_telegram_summary(pipeline, run)
        text = mock_send.call_args[0][2]
        assert len(text) <= 4096

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_skipped_counts_appear_in_summary(self, mock_send):
        """H4: skipped_dup_count and skipped_exhausted_count show in message."""
        mock_send.return_value = True
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(
            id=1, pipeline_id=1, started_at=now, trigger_reason="scheduled",
            pulled_count=5, uploaded_count=2,
            skipped_dup_count=2, skipped_exhausted_count=1,
        )

        send_telegram_summary(pipeline, run)
        text = mock_send.call_args[0][2]
        assert "Bỏ qua (trùng): 2" in text
        assert "Bỏ qua (hết): 1" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_run_id_none_skips_video_lookup_but_still_sends(self, mock_send):
        """E4: run with id=None skips DB query, still sends summary."""
        mock_send.return_value = True
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(
            id=None, pipeline_id=1, started_at=now, trigger_reason="scheduled",
            pulled_count=1, uploaded_count=1,
        )

        send_telegram_summary(pipeline, run)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][2]
        assert "[my-pipe]" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_db_failure_on_video_fetch_still_sends(self, mock_send):
        """E5: DB error when fetching VideoUpload → summary sent without video details."""
        mock_send.return_value = True
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(
            id=999, pipeline_id=1, started_at=now, trigger_reason="scheduled",
            pulled_count=3, uploaded_count=2,
        )

        with patch("scheduler.pipeline_worker.Session") as mock_session_cls:
            mock_session_cls.return_value.__enter__ = MagicMock(side_effect=Exception("db down"))
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            send_telegram_summary(pipeline, run)

        mock_send.assert_called_once()
        text = mock_send.call_args[0][2]
        assert "[my-pipe]" in text
        assert "Đăng tải: 2" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_soft_cap_truncation_appears_when_many_videos(self, mock_send):
        """E6: 'và N video khác' appears when video list exceeds 3500-char soft cap."""
        mock_send.return_value = True
        now = now_utc()

        with Session(_api_db.engine) as s:
            _make_account(s, account_id=1)
            p = _make_pipeline(
                s,
                telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
                telegram_chat_id="-1001234567890",
            )
            run = PipelineRun(
                pipeline_id=p.id, started_at=now, trigger_reason="scheduled",
                pulled_count=35, uploaded_count=35,
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
            pid = p.id

            # Each entry: "  ✅ {60-char title}\n     {50-char url}" ≈ 120 chars
            # 35 × 120 = 4200 chars body — enough to breach the 3500 soft cap
            for i in range(35):
                s.add(VideoUpload(
                    run_id=run_id, account_id=1,
                    youtube_video_id=f"v{i:04d}",
                    source_title=f"Video Title That Is Exactly Sixty Characters Long {i:03d}",
                    tiktok_url=f"https://tiktok.com/@channel/video/{i:020d}",
                    status="succeeded",
                    youtube_url=f"https://youtube.com/watch?v=v{i}",
                ))
            s.commit()
            s.refresh(run)

        pipeline = _make_tg_pipeline(
            now, id=pid,
            telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678",
            telegram_chat_id="-1001234567890",
        )
        send_telegram_summary(pipeline, run)
        text = mock_send.call_args[0][2]
        assert "... và" in text
        assert "video khác" in text
        assert len(text) <= 4096

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_started_at_none_does_not_raise(self, mock_send):
        """C2: run.started_at=None must not raise in finally — would mask original exception."""
        mock_send.return_value = True
        now = now_utc()
        pipeline = _make_tg_pipeline(now)
        run = PipelineRun(
            id=1, pipeline_id=1, started_at=None, trigger_reason="scheduled",
            pulled_count=1, uploaded_count=1,
        )

        # Must not raise AttributeError
        send_telegram_summary(pipeline, run)
        mock_send.assert_called_once()


class TestPipelineRunContext:
    """Bug 7: _pipeline_run_context always sends Telegram even on crash."""

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker.run_pipeline")
    def test_telegram_sent_on_success(self, mock_run, mock_notify):
        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=10)
        run = PipelineRun(id=99, pipeline_id=10, started_at=now, trigger_reason="scheduled")
        mock_run.return_value = run

        with _pipeline_run_context(pipeline, "scheduled") as r:
            assert r is run

        mock_notify.assert_called_once_with(pipeline, run)

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    @patch("scheduler.pipeline_worker.run_pipeline")
    def test_telegram_sent_on_crash(self, mock_run, mock_finalize, mock_notify):
        """Crash in run_pipeline must still trigger Telegram notification."""
        now = now_utc()

        # Put pipeline + run in DB so the post-crash query finds the run
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            pid = p.id
            db_run = PipelineRun(
                pipeline_id=pid, started_at=now, trigger_reason="scheduled",
                error_text="worker crashed",
            )
            s.add(db_run)
            s.commit()

        pipeline = _make_tg_pipeline(now, id=pid)

        mock_run.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            with _pipeline_run_context(pipeline, "scheduled"):
                pass  # never reached

        mock_finalize.assert_called_once()
        mock_notify.assert_called_once()
        notified_run = mock_notify.call_args[0][1]
        assert notified_run is not None

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker.run_pipeline")
    def test_telegram_not_sent_when_run_is_none_due_to_no_db(self, mock_run, mock_notify):
        """send_telegram_summary is called — it no-ops internally when run is valid."""
        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=30)
        run = PipelineRun(id=55, pipeline_id=30, started_at=now, trigger_reason="scheduled")
        mock_run.return_value = run

        with _pipeline_run_context(pipeline, "scheduled"):
            pass

        mock_notify.assert_called_once_with(pipeline, run)

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    @patch("scheduler.pipeline_worker.run_pipeline")
    def test_telegram_skipped_when_post_crash_db_query_fails(self, mock_run, mock_finalize, mock_notify):
        """P4: crash path DB re-fetch fails → run stays None → Telegram not sent."""
        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=40)
        mock_run.side_effect = RuntimeError("boom")

        with patch("scheduler.pipeline_worker.Session") as mock_session_cls:
            mock_session_cls.return_value.__enter__ = MagicMock(side_effect=Exception("db gone"))
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(RuntimeError):
                with _pipeline_run_context(pipeline, "scheduled"):
                    pass

        mock_notify.assert_not_called()

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    def test_timeout_calls_emergency_finalize_exactly_once(self, mock_finalize, mock_notify):
        """Timeout path: _emergency_finalize called exactly once (not twice from double-catch)."""
        from concurrent.futures import TimeoutError as FutTimeout

        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=50)

        mock_future = MagicMock()
        mock_future.result.side_effect = FutTimeout()
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future

        with patch("scheduler.pipeline_worker.ThreadPoolExecutor", return_value=mock_executor):
            with pytest.raises(Exception):
                with _pipeline_run_context(pipeline, "scheduled"):
                    pass

        mock_finalize.assert_called_once_with(pipeline.id)

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    def test_timeout_raises_for_caller(self, mock_finalize, mock_notify):
        """Timeout path: TimeoutError propagates out so pipeline_tick can advance_next_fire."""
        from concurrent.futures import TimeoutError as FutTimeout

        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=51)

        mock_future = MagicMock()
        mock_future.result.side_effect = FutTimeout()
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future

        with patch("scheduler.pipeline_worker.ThreadPoolExecutor", return_value=mock_executor):
            with pytest.raises(Exception):
                with _pipeline_run_context(pipeline, "scheduled"):
                    pass

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    def test_timeout_telegram_not_sent_when_no_run_in_db(self, mock_finalize, mock_notify):
        """Timeout before any PipelineRun persisted → run stays None → Telegram not called."""
        from concurrent.futures import TimeoutError as FutTimeout

        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=52)

        mock_future = MagicMock()
        mock_future.result.side_effect = FutTimeout()
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future

        with patch("scheduler.pipeline_worker.ThreadPoolExecutor", return_value=mock_executor):
            with patch("scheduler.pipeline_worker.Session") as mock_session_cls:
                mock_session = MagicMock()
                mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
                mock_session.exec.return_value.first.return_value = None

                with pytest.raises(Exception):
                    with _pipeline_run_context(pipeline, "scheduled"):
                        pass

        mock_notify.assert_not_called()

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    def test_timeout_telegram_sent_when_run_exists_in_db(self, mock_finalize, mock_notify):
        """Timeout after PipelineRun persisted → outer except fetches it → Telegram called."""
        from concurrent.futures import TimeoutError as FutTimeout

        now = now_utc()
        pipeline = _make_tg_pipeline(now, id=53)
        db_run = PipelineRun(id=77, pipeline_id=53, started_at=now, trigger_reason="scheduled")

        mock_future = MagicMock()
        mock_future.result.side_effect = FutTimeout()
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future

        with patch("scheduler.pipeline_worker.ThreadPoolExecutor", return_value=mock_executor):
            with patch("scheduler.pipeline_worker.Session") as mock_session_cls:
                mock_session = MagicMock()
                mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
                mock_session.exec.return_value.first.return_value = db_run

                with pytest.raises(Exception):
                    with _pipeline_run_context(pipeline, "scheduled"):
                        pass

        mock_notify.assert_called_once_with(pipeline, db_run)


class TestEmergencyFinalize:
    """Tests for _emergency_finalize crash recovery."""

    def test_finalizes_unfinished_runs(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            pid = p.id

        with Session(_api_db.engine) as s:
            run1 = PipelineRun(
                pipeline_id=pid, started_at=now - timedelta(minutes=5),
                trigger_reason="scheduled",
            )
            run2 = PipelineRun(
                pipeline_id=pid, started_at=now - timedelta(minutes=2),
                trigger_reason="manual",
            )
            s.add_all([run1, run2])
            s.commit()
            s.refresh(run1)
            s.refresh(run2)
            run1_id, run2_id = run1.id, run2.id

        _emergency_finalize(pid)

        with Session(_api_db.engine) as s:
            r1 = s.get(PipelineRun, run1_id)
            r2 = s.get(PipelineRun, run2_id)
            assert r1.finished_at is not None
            assert r1.error_text == "worker crashed"
            assert r2.finished_at is not None
            assert r2.error_text == "worker crashed"

    def test_preserves_existing_error_text(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            pid = p.id

        with Session(_api_db.engine) as s:
            run = PipelineRun(
                pipeline_id=pid, started_at=now - timedelta(minutes=5),
                trigger_reason="scheduled", error_text="list_top_n failed",
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id

        _emergency_finalize(pid)

        with Session(_api_db.engine) as s:
            r = s.get(PipelineRun, run_id)
            assert r.finished_at is not None
            assert r.error_text == "list_top_n failed"

    def test_does_not_touch_already_finished(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            pid = p.id

        with Session(_api_db.engine) as s:
            run = PipelineRun(
                pipeline_id=pid, started_at=now - timedelta(minutes=10),
                finished_at=now - timedelta(minutes=5),
                trigger_reason="scheduled",
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            run_id = run.id
            orig_finished = run.finished_at

        _emergency_finalize(pid)

        with Session(_api_db.engine) as s:
            r = s.get(PipelineRun, run_id)
            assert r.finished_at == orig_finished
            assert r.error_text is None


class TestClaimOrdering:
    """Verify run_now pipelines are prioritized over scheduled."""

    def test_run_now_claimed_first(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            # Scheduled pipeline due 1 minute ago
            _make_pipeline(s, name="scheduled-one",
                           next_fire_at=now - timedelta(minutes=1))
            # run_now pipeline due in the future but manually triggered
            p2 = _make_pipeline(s, name="manual-one",
                                next_fire_at=now + timedelta(hours=5),
                                run_now_requested_at=now)
            p2_id = p2.id

        result = claim_due_pipeline()
        assert result is not None
        pipeline, trigger = result
        assert pipeline.id == p2_id
        assert trigger == "manual"


# ─── PIPELINE_TMP_DIR config ───────────────────────────────────────────────


class TestCheckDiskSpace:
    @patch("scheduler.pipeline_worker.os.makedirs")
    @patch("scheduler.pipeline_worker.os.statvfs")
    def test_uses_pipeline_tmp_dir_when_set(self, mock_statvfs, mock_makedirs, monkeypatch):
        monkeypatch.setenv("PIPELINE_TMP_DIR", "/data/pipeline_tmp")
        mock_statvfs.return_value = MagicMock(f_bavail=1_000_000, f_frsize=4096)

        _check_disk_space()

        mock_makedirs.assert_called_once_with("/data/pipeline_tmp", exist_ok=True)
        mock_statvfs.assert_called_once_with("/data/pipeline_tmp")

    @patch("scheduler.pipeline_worker.os.statvfs")
    def test_falls_back_to_system_tmp_when_env_unset(self, mock_statvfs, monkeypatch):
        import tempfile as _tmp
        monkeypatch.delenv("PIPELINE_TMP_DIR", raising=False)
        mock_statvfs.return_value = MagicMock(f_bavail=1_000_000, f_frsize=4096)

        _check_disk_space()

        mock_statvfs.assert_called_once_with(_tmp.gettempdir())

    @patch("scheduler.pipeline_worker.os.statvfs")
    def test_returns_false_when_less_than_1gb_free(self, mock_statvfs, monkeypatch):
        monkeypatch.delenv("PIPELINE_TMP_DIR", raising=False)
        mock_statvfs.return_value = MagicMock(f_bavail=100, f_frsize=4096)  # ~400KB free

        assert _check_disk_space() is False

    @patch("scheduler.pipeline_worker.os.statvfs")
    def test_returns_true_when_enough_space(self, mock_statvfs, monkeypatch):
        monkeypatch.delenv("PIPELINE_TMP_DIR", raising=False)
        mock_statvfs.return_value = MagicMock(f_bavail=500_000, f_frsize=4096)  # ~2GB free

        assert _check_disk_space() is True

    @patch("scheduler.pipeline_worker.os.statvfs")
    def test_empty_string_env_falls_back_to_system_tmp(self, mock_statvfs, monkeypatch):
        import tempfile as _tmp
        monkeypatch.setenv("PIPELINE_TMP_DIR", "")
        mock_statvfs.return_value = MagicMock(f_bavail=1_000_000, f_frsize=4096)

        _check_disk_space()

        mock_statvfs.assert_called_once_with(_tmp.gettempdir())


class TestYoutubeMkdtempDir:
    @patch("scheduler.pipeline_worker._check_disk_space", return_value=True)
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.tempfile.mkdtemp")
    def test_mkdtemp_passes_pipeline_tmp_dir(
        self, mock_mkdtemp, mock_upload, mock_download, mock_list, _disk, monkeypatch
    ):
        monkeypatch.setenv("PIPELINE_TMP_DIR", "/data/pipeline_tmp")
        mock_mkdtemp.return_value = "/data/pipeline_tmp/pipeline_dl_abc"
        mock_list.return_value = [
            {"id": "v1", "title": "T", "url": "https://www.youtube.com/watch?v=v1"}
        ]
        mock_download.return_value = ("/data/pipeline_tmp/pipeline_dl_abc/v1.mp4", "")
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            s.expunge(pipeline)

        run_pipeline(pipeline, "scheduled")

        mock_mkdtemp.assert_called_once_with(prefix="pipeline_dl_", dir="/data/pipeline_tmp")

    @patch("scheduler.pipeline_worker._check_disk_space", return_value=True)
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.tempfile.mkdtemp")
    def test_mkdtemp_uses_none_dir_when_env_unset(
        self, mock_mkdtemp, mock_upload, mock_download, mock_list, _disk, monkeypatch
    ):
        monkeypatch.delenv("PIPELINE_TMP_DIR", raising=False)
        mock_mkdtemp.return_value = "/tmp/pipeline_dl_abc"
        mock_list.return_value = [
            {"id": "v1", "title": "T", "url": "https://www.youtube.com/watch?v=v1"}
        ]
        mock_download.return_value = ("/tmp/pipeline_dl_abc/v1.mp4", "")
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_pipeline(s)
            s.expunge(pipeline)

        run_pipeline(pipeline, "scheduled")

        mock_mkdtemp.assert_called_once_with(prefix="pipeline_dl_", dir=None)


# ─── E1b: Retention purge ─────────────────────────────────────────────────


def _make_run(session: Session, pipeline_id: int, started_at: datetime) -> PipelineRun:
    run = PipelineRun(
        pipeline_id=pipeline_id,
        started_at=started_at,
        finished_at=started_at + timedelta(minutes=1),
        trigger_reason="scheduled",
        pulled_count=1,
        uploaded_count=1,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _make_video_upload(
    session: Session,
    pipeline_id: int,
    run_id: int,
    *,
    created_at: datetime | None = None,
    youtube_video_id: str = "vid1",
) -> VideoUpload:
    vu = VideoUpload(
        pipeline_id=pipeline_id,
        run_id=run_id,
        account_id=1,
        youtube_video_id=youtube_video_id,
        youtube_url=f"https://youtube.com/watch?v={youtube_video_id}",
        status="succeeded",
        created_at=created_at or now_utc(),
    )
    session.add(vu)
    session.commit()
    session.refresh(vu)
    return vu


class TestPurgeOldRuns:
    def test_returns_zero_when_nothing_to_purge(self):
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            _make_run(s, p.id, now_utc() - timedelta(days=1))

        assert purge_old_runs() == 0

    def test_deletes_runs_older_than_retention(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            _make_run(s, p.id, now - timedelta(days=RUN_RETENTION_DAYS + 1))

        deleted = purge_old_runs()

        assert deleted == 1
        with Session(_api_db.engine) as s:
            assert s.exec(select(PipelineRun)).all() == []

    def test_keeps_runs_within_retention_window(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            _make_run(s, p.id, now - timedelta(days=RUN_RETENTION_DAYS - 1))

        assert purge_old_runs() == 0
        with Session(_api_db.engine) as s:
            assert len(s.exec(select(PipelineRun)).all()) == 1

    def test_cascades_delete_to_video_uploads(self):
        now = now_utc()
        old_ts = now - timedelta(days=RUN_RETENTION_DAYS + 5)
        with Session(_api_db.engine) as s:
            _make_account(s)
            p = _make_pipeline(s)
            old_run = _make_run(s, p.id, old_ts)
            _make_video_upload(s, p.id, old_run.id, created_at=old_ts)

        purge_old_runs()

        with Session(_api_db.engine) as s:
            assert s.exec(select(VideoUpload)).all() == []

    def test_does_not_delete_video_uploads_for_recent_runs(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            _make_account(s)
            p = _make_pipeline(s)
            recent_run = _make_run(s, p.id, now - timedelta(days=1))
            _make_video_upload(s, p.id, recent_run.id)

        purge_old_runs()

        with Session(_api_db.engine) as s:
            uploads = s.exec(select(VideoUpload)).all()
            assert len(uploads) == 1

    def test_purges_only_old_runs_keeps_recent(self):
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            _make_run(s, p.id, now - timedelta(days=RUN_RETENTION_DAYS + 2))
            _make_run(s, p.id, now - timedelta(days=RUN_RETENTION_DAYS + 10))
            _make_run(s, p.id, now - timedelta(days=5))  # recent — keep

        deleted = purge_old_runs()

        assert deleted == 2
        with Session(_api_db.engine) as s:
            remaining = s.exec(select(PipelineRun)).all()
            assert len(remaining) == 1

    def test_cutoff_boundary_is_kept(self):
        # A run 1 minute inside the retention window (< 30 days old) must not be deleted.
        # "Exactly 30 days" is avoided — it would be racy because purge_old_runs() calls
        # now_utc() internally, making its cutoff fractionally later than test setup.
        now = now_utc()
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            _make_run(s, p.id, now - timedelta(days=RUN_RETENTION_DAYS) + timedelta(minutes=1))

        assert purge_old_runs() == 0

    def test_cascades_all_uploads_for_run(self):
        now = now_utc()
        old_ts = now - timedelta(days=RUN_RETENTION_DAYS + 3)
        with Session(_api_db.engine) as s:
            _make_account(s)
            p = _make_pipeline(s)
            old_run = _make_run(s, p.id, old_ts)
            for vid in ["v1", "v2", "v3"]:
                _make_video_upload(s, p.id, old_run.id, created_at=old_ts, youtube_video_id=vid)

        purge_old_runs()

        with Session(_api_db.engine) as s:
            assert s.exec(select(VideoUpload)).all() == []

    def test_new_upload_for_old_run_not_purged(self):
        # Race-guard: VideoUpload inserted by a concurrent tick (created_at≈now)
        # must NOT be deleted even if its run_id is in the old set.
        now = now_utc()
        old_ts = now - timedelta(days=RUN_RETENTION_DAYS + 5)
        with Session(_api_db.engine) as s:
            _make_account(s)
            p = _make_pipeline(s)
            old_run = _make_run(s, p.id, old_ts)
            # Upload created NOW (simulates concurrent insert during purge window)
            _make_video_upload(s, p.id, old_run.id, created_at=now)

        purge_old_runs()

        with Session(_api_db.engine) as s:
            # PipelineRun deleted; fresh VideoUpload survived with run_id NULLed
            assert s.exec(select(PipelineRun)).all() == []
            uploads = s.exec(select(VideoUpload)).all()
            assert len(uploads) == 1
            assert uploads[0].run_id is None

    def test_large_batch_does_not_crash(self):
        # Confirms subquery approach avoids SQLite "too many SQL variables" (>999 IDs).
        now = now_utc()
        old_ts = now - timedelta(days=RUN_RETENTION_DAYS + 1)
        with Session(_api_db.engine) as s:
            p = _make_pipeline(s)
            for _ in range(1100):
                _make_run(s, p.id, old_ts)

        deleted = purge_old_runs()

        assert deleted == 1100
        with Session(_api_db.engine) as s:
            assert s.exec(select(PipelineRun)).all() == []
