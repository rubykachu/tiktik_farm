"""Verify the 3 new Phase 1 tables (ChannelPipeline, PipelineRun, VideoUpload)
and the composite indexes that back dedupe + timeline queries.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from api import db as _db
from api.db_migrations import apply_indexes
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    apply_indexes(eng)
    with Session(eng) as s:
        yield s


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestChannelPipeline:
    def test_create_and_load(self, session):
        p = ChannelPipeline(
            name="Cubez Daily",
            channel_url="https://youtube.com/@cubez/videos",
            fetch_count=3,
            interval_value=1,
            interval_unit="days",
            first_fire_at=_now(),
            next_fire_at=_now(),
            state="active",
            caption_template="{title} #cubez",
            upload_options_json="{}",
            account_ids_json="[1]",
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.id is not None
        assert p.state == "active"
        assert p.run_now_requested_at is None
        assert p.lock_until is None
        assert p.max_duration_seconds is None
        assert p.telegram_bot_token is None
        assert p.upload_delay_minutes == 0

    def test_nullable_fields_default_to_none(self, session):
        p = ChannelPipeline(
            name="x",
            channel_url="https://youtube.com/@x/videos",
            fetch_count=1,
            interval_value=1,
            interval_unit="hours",
            first_fire_at=_now(),
            next_fire_at=_now(),
            state="active",
            caption_template="{title}",
            upload_options_json="{}",
            account_ids_json="[1]",
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.telegram_chat_id is None
        assert p.run_now_requested_at is None


class TestPipelineRun:
    def test_create_with_pipeline(self, session):
        p = ChannelPipeline(
            name="x", channel_url="https://youtube.com/@x", fetch_count=1,
            interval_value=1, interval_unit="hours",
            first_fire_at=_now(), next_fire_at=_now(),
            state="active", caption_template="{title}",
            upload_options_json="{}", account_ids_json="[1]",
        )
        session.add(p)
        session.commit()
        session.refresh(p)

        r = PipelineRun(
            pipeline_id=p.id,
            started_at=_now(),
            trigger_reason="scheduled",
            pulled_count=0,
            uploaded_count=0,
            skipped_dup_count=0,
            skipped_exhausted_count=0,
            failed_count=0,
            summary_text="",
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        assert r.id is not None
        assert r.finished_at is None


class TestVideoUpload:
    def test_create_and_query_by_account_and_video(self, session):
        acct = Account(username="u1", cookie_path="/x")
        session.add(acct)
        session.commit()
        session.refresh(acct)

        vu = VideoUpload(
            account_id=acct.id,
            youtube_video_id="abc123",
            youtube_url="https://youtu.be/abc123",
            status="succeeded",
            attempt_count=1,
            created_at=_now(),
        )
        session.add(vu)
        session.commit()
        session.refresh(vu)
        assert vu.id is not None

        # Dedupe-style lookup.
        found = session.exec(
            select(VideoUpload).where(
                VideoUpload.account_id == acct.id,
                VideoUpload.youtube_video_id == "abc123",
                VideoUpload.status == "succeeded",
            )
        ).all()
        assert len(found) == 1


class TestCompositeIndexes:
    def test_dedupe_index_present(self, session):
        rows = session.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ) if hasattr(session, "exec_driver_sql") else None
        # Fallback to direct connection if SQLModel session lacks the helper.
        if rows is None:
            conn = session.connection()
            rows = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        names = {r[0] for r in rows}
        assert "ix_video_upload_account_video_status" in names
        assert "ix_video_upload_pipeline_created" in names
        assert "ix_video_upload_account_created" in names
