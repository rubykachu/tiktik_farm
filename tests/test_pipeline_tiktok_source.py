"""Pipelines whose source is TikTok.

TikTok reuses the YouTube tick end to end — the only difference is which
module scheduler.pipeline_worker pulls the videos from, chosen by
api.services.pipeline_sources from the pipeline's source_type. These tests
pin that choice, so a future edit cannot quietly route a TikTok pipeline
back through the YouTube module (which would refuse the URL) or the other
way round.
"""
import os
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, PipelineSeenVideo, VideoUpload
from api.services import pipeline_sources, tiktok_channel, youtube_channel
from scheduler.pipeline_worker import RunArtifacts, run_pipeline

PROFILE = "https://www.tiktok.com/@creator"
POST_URL = "https://www.tiktok.com/@creator/video/7001"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from api.db import make_engine, override_engine

    # tiktok_uploader imports call load_dotenv(), so the developer's real .env
    # would otherwise decide whether these runs publish download links.
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.setenv("MANUAL_DOWNLOAD_DIR", str(tmp_path / "store"))

    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))


def _make_pipeline(**overrides):
    now = now_utc()
    defaults = dict(
        name="tiktok-pipe",
        source_type="tiktok",
        channel_url=PROFILE,
        fetch_count=2,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now,
        next_fire_at=now,
        state="active",
        caption_template="{title}",
        upload_options_json="{}",
        account_ids_json="[]",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    with Session(_api_db.engine) as s:
        s.add(p)
        s.commit()
        s.refresh(p)
        s.expunge(p)
    return p


def _make_account(username="tiktok-acct"):
    with Session(_api_db.engine) as s:
        account = Account(
            username=username,
            cookie_path=f"/tmp/cookies/{username}.cookie",
            has_valid_session=True,
        )
        s.add(account)
        s.commit()
        s.refresh(account)
        return account.id


def _fake_download(url, temp_dir, max_duration_seconds):
    path = os.path.join(temp_dir, "post.mp4")
    with open(path, "wb") as fh:
        fh.write(b"\0" * 1024)
    return path, "a caption", ""


class TestSourceRegistry:
    def test_maps_source_type_to_module(self):
        assert pipeline_sources.get("youtube") is youtube_channel
        assert pipeline_sources.get("tiktok") is tiktok_channel

    def test_rows_written_before_source_type_existed_mean_youtube(self):
        assert pipeline_sources.get("") is youtube_channel
        assert pipeline_sources.get(None) is youtube_channel

    def test_douyin_is_not_here(self):
        """Douyin downloads through a vendor subprocess and is routed to its
        own path before the worker reaches this registry."""
        assert "douyin" not in pipeline_sources.SOURCES


class TestTikTokTick:
    @patch("api.services.tiktok_channel.list_top_n")
    @patch("api.services.tiktok_channel.download")
    @patch("api.services.youtube_channel.list_top_n")
    def test_pulls_from_tiktok_not_youtube(self, mock_yt_list, mock_download, mock_list):
        mock_list.return_value = [{"id": "7001", "title": "T1", "url": POST_URL}]
        mock_download.side_effect = _fake_download

        run = run_pipeline(_make_pipeline(), "scheduled", RunArtifacts())

        mock_list.assert_called_once()
        mock_yt_list.assert_not_called()
        assert run.pulled_count == 1

    @patch("api.services.tiktok_channel.list_top_n")
    @patch("api.services.tiktok_channel.download")
    def test_download_only_run_records_seen_videos(self, mock_download, mock_list):
        mock_list.return_value = [{"id": "7001", "title": "T1", "url": POST_URL}]
        mock_download.side_effect = _fake_download

        run_pipeline(_make_pipeline(), "scheduled", RunArtifacts())

        with Session(_api_db.engine) as s:
            seen = s.exec(select(PipelineSeenVideo)).all()
        assert [row.source_key for row in seen] == ["7001"]

    @patch("api.services.tiktok_channel.list_top_n")
    @patch("api.services.tiktok_channel.download")
    def test_already_seen_post_is_not_downloaded_again(self, mock_download, mock_list):
        mock_list.return_value = [{"id": "7001", "title": "T1", "url": POST_URL}]
        mock_download.side_effect = _fake_download
        pipeline = _make_pipeline()

        run_pipeline(pipeline, "scheduled", RunArtifacts())
        assert mock_download.call_count == 1

        run_pipeline(pipeline, "scheduled", RunArtifacts())
        assert mock_download.call_count == 1

    @patch("api.services.tiktok_channel.list_top_n")
    @patch("api.services.tiktok_channel.download")
    def test_entry_without_a_url_falls_back_to_a_tiktok_url(self, mock_download, mock_list):
        """The fallback must come from the source module — a YouTube watch
        URL here would send the downloader to the wrong site."""
        mock_list.return_value = [{"id": "7001", "title": "T1"}]
        mock_download.side_effect = _fake_download
        artifacts = RunArtifacts()

        run_pipeline(_make_pipeline(), "scheduled", artifacts)

        assert artifacts.videos[0].source_url == "https://www.tiktok.com/@/video/7001"


class TestTikTokUpload:
    @patch("api.services.tiktok_channel.list_top_n")
    @patch("api.services.tiktok_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_uploads_and_records_the_post_id(self, mock_upload, mock_download, mock_list):
        account_id = _make_account()

        mock_list.return_value = [{"id": "7001", "title": "T1", "url": POST_URL}]
        mock_download.side_effect = _fake_download
        mock_upload.return_value = (True, "pub1", "https://tiktok.com/pub1")

        run = run_pipeline(
            _make_pipeline(account_ids_json=f"[{account_id}]"), "scheduled", RunArtifacts()
        )

        assert run.uploaded_count == 1
        with Session(_api_db.engine) as s:
            rows = s.exec(select(VideoUpload)).all()
        assert [r.youtube_video_id for r in rows] == ["7001"]
        assert rows[0].status == "succeeded"

    @patch("api.services.tiktok_channel.list_top_n")
    @patch("api.services.tiktok_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_succeeded_post_is_not_uploaded_twice(self, mock_upload, mock_download, mock_list):
        account_id = _make_account()

        mock_list.return_value = [{"id": "7001", "title": "T1", "url": POST_URL}]
        mock_download.side_effect = _fake_download
        mock_upload.return_value = (True, "pub1", "https://tiktok.com/pub1")
        pipeline = _make_pipeline(account_ids_json=f"[{account_id}]")

        run_pipeline(pipeline, "scheduled", RunArtifacts())
        run_pipeline(pipeline, "scheduled", RunArtifacts())

        assert mock_upload.call_count == 1
