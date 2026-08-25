"""LLM integration with the pipeline worker (T6).

- Worker calls llm.rewrite_title once per video, even with multiple accounts.
- LLM failure falls back to the raw YouTube title.
- Worker skips LLM when llm.enabled is false or no default model exists.
- The rewritten value substitutes for {ai_title} in render_caption.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, LLMModel, VideoUpload
from api.services import secrets as _secrets


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from api.db import make_engine, override_engine

    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
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
        id=account_id,
        username=username,
        cookie_path=f"/tmp/{username}.cookie",
        has_valid_session=True,
    )
    s.add(a)
    s.commit()
    return a


def _pipeline(s, **overrides):
    now = now_utc()
    defaults = dict(
        name="p",
        channel_url="https://www.youtube.com/@x/videos",
        fetch_count=2,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now - timedelta(hours=6),
        next_fire_at=now - timedelta(minutes=1),
        state="active",
        caption_template="{ai_title} #t",
        caption_ai_prompt="Rewrite as clickbait",
        upload_options_json="{}",
        account_ids_json="[1]",
    )
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    s.add(p)
    s.commit()
    s.refresh(p)
    for f in (
        p.id, p.name, p.channel_url, p.fetch_count, p.interval_value,
        p.interval_unit, p.first_fire_at, p.next_fire_at, p.state,
        p.caption_template, p.caption_ai_prompt, p.upload_options_json,
        p.account_ids_json, p.max_duration_seconds, p.upload_delay_minutes,
        p.telegram_bot_token, p.telegram_chat_id,
        p.run_now_requested_at, p.lock_until, p.created_at, p.updated_at,
    ):
        pass
    return p


def _default_llm(s):
    row = LLMModel(
        label="Gemini",
        model_id="gemini/gemini-2.0-flash-lite",
        api_key_encrypted=_secrets.encrypt("test-api-key"),
        is_default=True,
    )
    s.add(row)
    s.commit()
    s.refresh(row)
    return row


class TestLLMRewriteHappy:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_rewrite_called_once_per_video_across_accounts(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "raw1", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "raw2", "url": "https://www.youtube.com/watch?v=v2"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "long description")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_rewrite.side_effect = lambda prompt, title, description, **kw: f"AI-{title}"

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s, 1, "u1")
            _account(s, 2, "u2")
            _default_llm(s)
            p = _pipeline(s, account_ids_json="[1, 2]")
            s.expunge(p)

        run = run_pipeline(p, "scheduled")

        assert run.uploaded_count == 4  # 2 videos × 2 accounts
        # 2 unique videos → 2 rewrite calls regardless of account count.
        assert mock_rewrite.call_count == 2

        with Session(_api_db.engine) as s:
            uploads = s.exec(select(VideoUpload)).all()
            captions = {u.youtube_video_id: u.rendered_caption for u in uploads}
        assert captions["v1"].startswith("AI-raw1")
        assert captions["v2"].startswith("AI-raw2")

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_description_forwarded_to_llm(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "t", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "DESCRIPTION-FROM-YT")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_rewrite.return_value = "AI"

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            _default_llm(s)
            p = _pipeline(s)
            s.expunge(p)

        run_pipeline(p, "scheduled")

        assert mock_rewrite.called
        kw = mock_rewrite.call_args.kwargs
        args = mock_rewrite.call_args.args
        assert (kw.get("description") == "DESCRIPTION-FROM-YT") or (
            len(args) >= 3 and "DESCRIPTION-FROM-YT" in args[2]
        )


class TestLLMRewriteFallback:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_llm_call_failed_falls_back_to_raw_title(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        from api.services.llm import LLMCallFailed

        mock_list.return_value = [
            {"id": "v1", "title": "raw-title", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_rewrite.side_effect = LLMCallFailed("upstream 500")

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            _default_llm(s)
            p = _pipeline(s)
            s.expunge(p)

        run = run_pipeline(p, "scheduled")

        assert run.uploaded_count == 1
        assert run.failed_count == 0  # LLM failure does not fail the run

        with Session(_api_db.engine) as s:
            up = s.exec(select(VideoUpload)).first()
            assert up.rendered_caption.startswith("raw-title")

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_no_llm_call_when_template_has_no_ai_title(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "t", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            _default_llm(s)
            p = _pipeline(s, caption_template="{title} #plain")
            s.expunge(p)

        run_pipeline(p, "scheduled")
        mock_rewrite.assert_not_called()

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_no_llm_when_no_default_model_set(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "raw", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            # No LLMModel.is_default row.
            p = _pipeline(s)
            s.expunge(p)

        run = run_pipeline(p, "scheduled")
        mock_rewrite.assert_not_called()
        assert run.uploaded_count == 1
        with Session(_api_db.engine) as s:
            up = s.exec(select(VideoUpload)).first()
            assert up.rendered_caption.startswith("raw")

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_no_llm_when_globally_disabled(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        from api.services.llm_config import set_llm_enabled

        mock_list.return_value = [
            {"id": "v1", "title": "raw", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            _default_llm(s)
            p = _pipeline(s)
            s.expunge(p)

        set_llm_enabled(False)

        run_pipeline(p, "scheduled")
        mock_rewrite.assert_not_called()


class TestChannelDescriptionForwarding:
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_channel_description_forwarded_to_llm(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "t", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "video description")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_rewrite.return_value = "AI title"

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            _default_llm(s)
            p = _pipeline(s, channel_description="food review channel from China")
            s.expunge(p)

        run_pipeline(p, "scheduled")

        assert mock_rewrite.called
        call_kwargs = mock_rewrite.call_args.kwargs
        assert call_kwargs.get("channel_description") == "food review channel from China"

    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("api.services.llm_rewrite.llm.rewrite_title")
    def test_null_channel_description_forwarded_as_empty_string(
        self, mock_rewrite, mock_list, mock_download, mock_upload
    ):
        mock_list.return_value = [
            {"id": "v1", "title": "t", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.return_value = ("/tmp/x.mp4", "")
        mock_upload.return_value = (True, "tk", "https://tiktok.com/v/tk")
        mock_rewrite.return_value = "AI title"

        from scheduler.pipeline_worker import run_pipeline

        with Session(_api_db.engine) as s:
            _account(s)
            _default_llm(s)
            p = _pipeline(s, channel_description=None)
            s.expunge(p)

        run_pipeline(p, "scheduled")

        call_kwargs = mock_rewrite.call_args.kwargs
        assert call_kwargs.get("channel_description") == ""
