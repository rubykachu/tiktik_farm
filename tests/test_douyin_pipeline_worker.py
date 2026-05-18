"""Tests for Pass 3 — Douyin pipeline support in scheduler/pipeline_worker.py."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, DouyinCookie, PipelineRun, VideoUpload
from api.services.douyin_channel import DownloadedVideo


COOKIE_DICT = {
    "ttwid": "abc123",
    "msToken": "tok456",
    "odin_tt": "odin789",
    "passport_csrf_token": "csrf000",
    "sid_guard": "sid111",
}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from api.db import make_engine, override_engine
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from api.services import secrets as _sec
    _sec._reset_for_tests()

    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))
    _sec._reset_for_tests()


def _make_account(session: Session, account_id: int = 1, username: str = "user1") -> Account:
    acct = Account(
        id=account_id,
        username=username,
        cookie_path=f"/tmp/{username}.cookie",
        has_valid_session=True,
    )
    session.add(acct)
    session.commit()
    return acct


def _make_douyin_cookie(session: Session) -> DouyinCookie:
    from api.services import secrets
    encrypted = secrets.encrypt(json.dumps(COOKIE_DICT))
    row = DouyinCookie(name="Test Cookie", cookie_json_encrypted=encrypted)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _make_douyin_pipeline(session: Session, cookie_id: int, **overrides) -> ChannelPipeline:
    now = now_utc()
    defaults = dict(
        name="douyin-test",
        channel_url="https://www.douyin.com/user/MS4wLjAB",
        fetch_count=3,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now - timedelta(hours=6),
        next_fire_at=now - timedelta(minutes=1),
        state="active",
        source_type="douyin",
        douyin_cookie_id=cookie_id,
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
    _ = (p.id, p.name, p.source_type, p.douyin_cookie_id, p.account_ids_json,
         p.caption_template, p.upload_options_json, p.fetch_count, p.channel_url,
         p.max_duration_seconds, p.caption_ai_prompt, p.upload_delay_minutes,
         p.telegram_bot_token, p.telegram_chat_id, p.interval_value, p.interval_unit,
         p.next_fire_at, p.lock_until, p.run_now_requested_at)
    return p


def _fake_video(aweme_id: str, duration_seconds: int = 30) -> DownloadedVideo:
    return DownloadedVideo(
        aweme_id=aweme_id,
        file_path=f"/tmp/{aweme_id}.mp4",
        title=f"title of {aweme_id}",
        description=f"desc of {aweme_id}",
        duration_seconds=duration_seconds,
    )


class TestRunDouyinPipeline:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_happy_path_records_video_uploads(self, mock_dl, mock_upload):
        mock_dl.return_value = [_fake_video("aaa111"), _fake_video("bbb222")]
        mock_upload.return_value = (True, "tk1", "https://tiktok.com/video/tk1")

        with Session(_api_db.engine) as s:
            _make_account(s)
            cookie = _make_douyin_cookie(s)
            pipeline = _make_douyin_pipeline(s, cookie.id)
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run = run_pipeline(pipeline, "scheduled")

        assert run.pulled_count == 2
        assert run.uploaded_count == 2
        assert run.finished_at is not None

        with Session(_api_db.engine) as s:
            uploads = s.exec(select(VideoUpload)).all()
            assert len(uploads) == 2
            assert all(u.status == "succeeded" for u in uploads)
            aweme_ids = {u.douyin_aweme_id for u in uploads}
            assert aweme_ids == {"aaa111", "bbb222"}

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_youtube_fields_empty_for_douyin(self, mock_dl, mock_upload):
        """VideoUpload rows for Douyin should have youtube_video_id="" and douyin_aweme_id set."""
        mock_dl.return_value = [_fake_video("ccc333")]
        mock_upload.return_value = (True, "tk2", "https://tiktok.com/video/tk2")

        with Session(_api_db.engine) as s:
            _make_account(s)
            cookie = _make_douyin_cookie(s)
            pipeline = _make_douyin_pipeline(s, cookie.id)
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run_pipeline(pipeline, "scheduled")

        with Session(_api_db.engine) as s:
            upload = s.exec(select(VideoUpload)).first()
            assert upload is not None
            assert upload.douyin_aweme_id == "ccc333"
            assert upload.youtube_video_id == ""

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_dedup_skips_already_succeeded(self, mock_dl, mock_upload):
        """Aweme already uploaded successfully should be skipped (not uploaded again)."""
        mock_dl.return_value = [_fake_video("ddd444")]
        mock_upload.return_value = (True, "tk3", "https://tiktok.com/video/tk3")

        with Session(_api_db.engine) as s:
            _make_account(s)
            cookie = _make_douyin_cookie(s)
            pipeline = _make_douyin_pipeline(s, cookie.id)
            pipeline_id = pipeline.id
            # Pre-insert a succeeded upload for this aweme
            existing = VideoUpload(
                pipeline_id=pipeline_id,
                run_id=None,
                account_id=1,
                youtube_video_id="",
                youtube_url="",
                douyin_aweme_id="ddd444",
                status="succeeded",
            )
            s.add(existing)
            s.commit()

        with Session(_api_db.engine) as s:
            pipeline = s.get(ChannelPipeline, pipeline_id)
            _ = (pipeline.id, pipeline.name, pipeline.source_type, pipeline.douyin_cookie_id,
                 pipeline.account_ids_json, pipeline.caption_template, pipeline.upload_options_json,
                 pipeline.fetch_count, pipeline.channel_url, pipeline.max_duration_seconds,
                 pipeline.caption_ai_prompt, pipeline.upload_delay_minutes,
                 pipeline.telegram_bot_token, pipeline.telegram_chat_id,
                 pipeline.interval_value, pipeline.interval_unit, pipeline.next_fire_at,
                 pipeline.lock_until, pipeline.run_now_requested_at)
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run = run_pipeline(pipeline, "scheduled")

        # Upload should not be called again
        mock_upload.assert_not_called()
        assert run.skipped_dup_count == 1

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_download_called_with_cookie_dict(self, mock_dl, mock_upload):
        """download_creator_posts must receive the decrypted cookie dict."""
        mock_dl.return_value = []
        mock_upload.return_value = (True, "tk4", "https://tiktok.com/video/tk4")

        with Session(_api_db.engine) as s:
            _make_account(s)
            cookie = _make_douyin_cookie(s)
            pipeline = _make_douyin_pipeline(s, cookie.id)
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run_pipeline(pipeline, "scheduled")

        assert mock_dl.called
        _, kwargs = mock_dl.call_args
        passed_cookies = kwargs.get("cookie_dict") or mock_dl.call_args[0][2]
        assert passed_cookies["ttwid"] == "abc123"

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_missing_cookie_id_records_error(self, mock_dl, mock_upload):
        """Pipeline with douyin_cookie_id pointing to deleted cookie records error, no crash."""
        mock_dl.return_value = []

        with Session(_api_db.engine) as s:
            _make_account(s)
            pipeline = _make_douyin_pipeline(s, cookie_id=9999)  # non-existent
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run = run_pipeline(pipeline, "scheduled")

        assert run.finished_at is not None
        assert run.error_text is not None
        mock_dl.assert_not_called()

    @patch("scheduler.pipeline_worker.douyin_channel.cleanup_aweme_files")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_cleanup_called_after_all_accounts(self, mock_dl, mock_upload, mock_cleanup):
        """cleanup_aweme_files must be called after all accounts finish uploading a video."""
        mock_dl.return_value = [_fake_video("eee555")]
        mock_upload.return_value = (True, "tk5", "https://tiktok.com/video/tk5")

        with Session(_api_db.engine) as s:
            _make_account(s, 1, "user1")
            cookie = _make_douyin_cookie(s)
            pipeline = _make_douyin_pipeline(s, cookie.id, account_ids_json="[1]")
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run_pipeline(pipeline, "scheduled")

        assert mock_cleanup.called
        args = mock_cleanup.call_args[0]
        assert args[1] == "eee555"

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    def test_upload_failure_recorded(self, mock_dl, mock_upload):
        """Failed uploads are recorded with status=failed."""
        mock_dl.return_value = [_fake_video("fff666")]
        mock_upload.return_value = (False, None, None)

        with Session(_api_db.engine) as s:
            _make_account(s)
            cookie = _make_douyin_cookie(s)
            pipeline = _make_douyin_pipeline(s, cookie.id)
            s.expunge(pipeline)

        from scheduler.pipeline_worker import run_pipeline
        run = run_pipeline(pipeline, "scheduled")

        assert run.failed_count == 1
        with Session(_api_db.engine) as s:
            upload = s.exec(select(VideoUpload)).first()
            assert upload.status == "failed"
            assert upload.douyin_aweme_id == "fff666"
