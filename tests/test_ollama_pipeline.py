"""Tests for pipeline branching on llm_mode (Task 4).

- mode=offline → llm.rewrite_title_offline called, not rewrite_title.
- mode=cloud → existing path (rewrite_title called, not offline).
- offline failure → raw title (no crash).
- llm disabled → neither called.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
import json

from api.models import Account, ChannelPipeline, DouyinCookie, LLMModel, VideoUpload
from api.services import secrets as _secrets
from api.services.douyin_channel import DownloadedVideo


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from api.db import make_engine, override_engine

    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    _secrets._reset_for_tests()

    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)

    from api.db_migrations import apply_indexes, apply_schema_patches, seed_app_settings
    apply_schema_patches(engine)
    apply_indexes(engine)
    seed_app_settings(engine)

    yield engine

    override_engine(make_engine("sqlite:///"))
    _secrets._reset_for_tests()


def _account(s, account_id=1, username="u1"):
    a = Account(
        id=account_id, username=username,
        cookie_path=f"/tmp/{username}.cookie", has_valid_session=True,
    )
    s.add(a); s.commit(); return a


def _pipeline(s, **overrides):
    now = now_utc()
    defaults = dict(
        name="p", channel_url="https://www.youtube.com/@x/videos",
        fetch_count=1, interval_value=6, interval_unit="hours",
        first_fire_at=now - timedelta(hours=6),
        next_fire_at=now - timedelta(minutes=1),
        state="active", caption_template="{ai_title} #t",
        caption_ai_prompt="Rewrite as clickbait",
        upload_options_json="{}", account_ids_json="[1]",
    )
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    s.add(p); s.commit(); s.refresh(p)
    return p


def _default_llm(s):
    row = LLMModel(
        label="Gemini", model_id="gemini/x",
        api_key_encrypted=_secrets.encrypt("k"), is_default=True,
    )
    s.add(row); s.commit(); return row


class TestOfflineMode:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("api.services.youtube_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_offline_mode_calls_rewrite_title_offline(
        self, mock_offline, mock_cloud, mock_list, mock_download, mock_upload
    ):
        from api.services.llm_config import set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_list.return_value = [{"id": "v1", "title": "raw", "url": "u"}]
        mock_download.return_value = ("/tmp/x.mp4", "desc", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_offline.return_value = "AI-offline"

        set_llm_mode("offline")

        with Session(_api_db.engine) as s:
            _account(s); p = _pipeline(s); s.expunge(p)

        run_pipeline(p, "scheduled")

        mock_offline.assert_called_once()
        mock_cloud.assert_not_called()

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("api.services.youtube_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_offline_mode_uses_env_model_and_url(
        self, mock_offline, mock_cloud, mock_list, mock_download, mock_upload
    ):
        from api.services.llm_config import set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_list.return_value = [{"id": "v1", "title": "raw", "url": "u"}]
        mock_download.return_value = ("/tmp/x.mp4", "desc", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_offline.return_value = "AI"

        set_llm_mode("offline")

        with Session(_api_db.engine) as s:
            _account(s); p = _pipeline(s); s.expunge(p)

        run_pipeline(p, "scheduled")

        kw = mock_offline.call_args.kwargs
        assert kw["model"] == "llama3.2"
        assert kw["base_url"] == "http://localhost:11434"

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("api.services.youtube_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_offline_failure_falls_back_to_raw_title(
        self, mock_offline, mock_list, mock_download, mock_upload
    ):
        from api.services.llm import LLMCallFailed
        from api.services.llm_config import set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_list.return_value = [{"id": "v1", "title": "raw-title", "url": "u"}]
        mock_download.return_value = ("/tmp/x.mp4", "", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_offline.side_effect = LLMCallFailed("conn refused")

        set_llm_mode("offline")

        with Session(_api_db.engine) as s:
            _account(s); p = _pipeline(s); s.expunge(p)

        run = run_pipeline(p, "scheduled")
        assert run.failed_count == 0

        with Session(_api_db.engine) as s:
            up = s.exec(select(VideoUpload)).first()
            assert up.rendered_caption.startswith("raw-title")

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("api.services.youtube_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_cloud_mode_does_not_call_offline(
        self, mock_offline, mock_cloud, mock_list, mock_download, mock_upload
    ):
        from scheduler.pipeline_worker import run_pipeline

        mock_list.return_value = [{"id": "v1", "title": "raw", "url": "u"}]
        mock_download.return_value = ("/tmp/x.mp4", "desc", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_cloud.return_value = "AI-cloud"

        # default mode = cloud, add a default LLM model
        with Session(_api_db.engine) as s:
            _account(s); _default_llm(s); p = _pipeline(s); s.expunge(p)

        run_pipeline(p, "scheduled")

        mock_offline.assert_not_called()
        mock_cloud.assert_called_once()

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("api.services.youtube_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_offline_skipped_when_llm_disabled(
        self, mock_offline, mock_list, mock_download, mock_upload
    ):
        from api.services.llm_config import set_llm_enabled, set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_list.return_value = [{"id": "v1", "title": "raw", "url": "u"}]
        mock_download.return_value = ("/tmp/x.mp4", "", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")

        set_llm_mode("offline")
        set_llm_enabled(False)

        with Session(_api_db.engine) as s:
            _account(s); p = _pipeline(s); s.expunge(p)

        run_pipeline(p, "scheduled")
        mock_offline.assert_not_called()


class TestOfflineModeEdgeCases:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("api.services.youtube_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_empty_offline_response_falls_back_to_raw_title(
        self, mock_offline, mock_list, mock_download, mock_upload
    ):
        from api.services.llm_config import set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_list.return_value = [{"id": "v1", "title": "raw-title", "url": "u"}]
        mock_download.return_value = ("/tmp/x.mp4", "", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_offline.return_value = ""  # empty string from Ollama

        set_llm_mode("offline")

        with Session(_api_db.engine) as s:
            _account(s); p = _pipeline(s); s.expunge(p)

        run_pipeline(p, "scheduled")

        with Session(_api_db.engine) as s:
            up = s.exec(select(VideoUpload)).first()
            assert up.rendered_caption.startswith("raw-title")


class TestDouyinOfflineMode:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_douyin_offline_mode_calls_rewrite_title_offline(
        self, mock_offline, mock_dl, mock_upload
    ):
        from api.services.llm_config import set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_dl.return_value = [
            DownloadedVideo(
                aweme_id="aa1",
                file_path="/tmp/aa1.mp4",
                title="douyin raw title",
                description="desc",
                duration_seconds=30,
            )
        ]
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_offline.return_value = "AI-douyin"

        set_llm_mode("offline")

        with Session(_api_db.engine) as s:
            _account(s)
            encrypted = _secrets.encrypt(json.dumps({"ttwid": "x"}))
            cookie = DouyinCookie(name="c", cookie_json_encrypted=encrypted)
            s.add(cookie); s.commit(); s.refresh(cookie)
            p = _pipeline(
                s,
                source_type="douyin",
                channel_url="https://www.douyin.com/user/MS4wLjAB",
                douyin_cookie_id=cookie.id,
                caption_template="{ai_title}",
                caption_ai_prompt="Rewrite",
            )
            s.expunge(p)

        run_pipeline(p, "scheduled")

        mock_offline.assert_called_once()

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.douyin_channel.download_creator_posts")
    @patch("api.services.llm_rewrite.llm.rewrite_title_offline")
    def test_douyin_offline_failure_falls_back_to_raw_title(
        self, mock_offline, mock_dl, mock_upload
    ):
        from api.services.llm import LLMCallFailed
        from api.services.llm_config import set_llm_mode
        from scheduler.pipeline_worker import run_pipeline

        mock_dl.return_value = [
            DownloadedVideo(
                aweme_id="bb2",
                file_path="/tmp/bb2.mp4",
                title="douyin raw title",
                description="desc",
                duration_seconds=30,
            )
        ]
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_offline.side_effect = LLMCallFailed("conn refused")

        set_llm_mode("offline")

        with Session(_api_db.engine) as s:
            _account(s)
            encrypted = _secrets.encrypt(json.dumps({"ttwid": "x"}))
            cookie = DouyinCookie(name="c", cookie_json_encrypted=encrypted)
            s.add(cookie); s.commit(); s.refresh(cookie)
            p = _pipeline(
                s,
                source_type="douyin",
                channel_url="https://www.douyin.com/user/MS4wLjAB",
                douyin_cookie_id=cookie.id,
                caption_template="{ai_title}",
                caption_ai_prompt="Rewrite",
            )
            s.expunge(p)

        run = run_pipeline(p, "scheduled")
        assert run.failed_count == 0

        with Session(_api_db.engine) as s:
            up = s.exec(select(VideoUpload)).first()
            assert up.rendered_caption.startswith("douyin raw title")
