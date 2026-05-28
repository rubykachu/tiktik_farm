"""Tests for scheduler/pipeline_worker.py — E1 through E5."""
import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
from scheduler.pipeline_worker import (
    reap_orphans,
    claim_due_pipeline,
    run_pipeline,
    advance_next_fire,
    release_lock,
    send_telegram_summary,
    pipeline_tick,
    _emergency_finalize,
    _check_disk_space,
    _next_fire_in_window,
    LockHeartbeat,
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
         p.run_now_requested_at, p.lock_until, p.created_at, p.updated_at)
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
    def _make_window_pipeline(self, session: Session, next_fire_at: datetime) -> ChannelPipeline:
        return _make_pipeline(
            session,
            interval_value=1,
            interval_unit="days",
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
            result = fresh.next_fire_at.replace(tzinfo=timezone.utc) if fresh.next_fire_at.tzinfo is None else fresh.next_fire_at
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
            result = fresh.next_fire_at.replace(tzinfo=timezone.utc) if fresh.next_fire_at.tzinfo is None else fresh.next_fire_at
            expected = datetime(2026, 6, 1, 0, 30, 0, tzinfo=timezone.utc)
            assert result == expected

    def test_past_window_jumps_to_next_cycle(self):
        # next_fire_at = 00:30, delay=5 → candidate 00:35 > window_close 00:30 → use interval
        base = datetime(2026, 6, 1, 0, 30, 0, tzinfo=timezone.utc)
        with Session(_api_db.engine) as s:
            pipeline = self._make_window_pipeline(s, next_fire_at=base)
            s.expunge(pipeline)

        advance_next_fire(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            fresh = s.get(ChannelPipeline, pipeline.id)
            result = fresh.next_fire_at.replace(tzinfo=timezone.utc) if fresh.next_fire_at.tzinfo is None else fresh.next_fire_at
            # next cycle = base + 1 day = 2026-06-02 00:30
            assert result >= base + timedelta(days=1)

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
            result = fresh.next_fire_at.replace(tzinfo=timezone.utc) if fresh.next_fire_at.tzinfo is None else fresh.next_fire_at
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


# ─── E5: Telegram summary ──────────────────────────────────────────────────


class TestTelegramSummary:
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_sends_when_configured(self, mock_send):
        mock_send.return_value = True
        now = now_utc()

        pipeline = ChannelPipeline(
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

        run = PipelineRun(
            id=1,
            pipeline_id=1,
            started_at=now,
            finished_at=now,
            trigger_reason="scheduled",
            pulled_count=3,
            uploaded_count=2,
            failed_count=1,
        )

        send_telegram_summary(pipeline, run)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][2]
        assert "[my-pipe]" in text
        assert "Pulled: 3" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_skips_when_no_config(self, mock_send):
        now = now_utc()
        pipeline = ChannelPipeline(
            id=1,
            name="no-tg",
            channel_url="https://www.youtube.com/@x",
            fetch_count=3,
            interval_value=6,
            interval_unit="hours",
            first_fire_at=now,
            next_fire_at=now,
            state="active",
            caption_template="{title}",
            created_at=now,
            updated_at=now,
        )
        run = PipelineRun(
            id=1, pipeline_id=1, started_at=now, trigger_reason="scheduled",
        )

        send_telegram_summary(pipeline, run)
        mock_send.assert_not_called()

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_failure_does_not_raise(self, mock_send):
        mock_send.side_effect = Exception("network error")
        now = now_utc()

        pipeline = ChannelPipeline(
            id=1,
            name="fail-tg",
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
        run = PipelineRun(
            id=1, pipeline_id=1, started_at=now, trigger_reason="scheduled",
        )

        # Should not raise
        send_telegram_summary(pipeline, run)


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
            assert r1.error_text == "worker_crash"
            assert r2.finished_at is not None
            assert r2.error_text == "worker_crash"

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
    @patch("scheduler.pipeline_worker.os.statvfs")
    def test_uses_pipeline_tmp_dir_when_set(self, mock_statvfs, monkeypatch):
        monkeypatch.setenv("PIPELINE_TMP_DIR", "/data/pipeline_tmp")
        mock_statvfs.return_value = MagicMock(f_bavail=1_000_000, f_frsize=4096)

        _check_disk_space()

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
