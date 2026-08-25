"""Pipelines with no TikTok account: download → Telegram → delete.

Covers the download-only tick, the credentials/attachment blocks in the
notification, and the deletion ordering that keeps the files alive until
Telegram has had its chance to attach them.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ChannelPipeline, PipelineRun, PipelineSeenVideo
from api.services import secrets
from scheduler.pipeline_worker import (
    RunArtifacts,
    RunVideo,
    _notify_pipeline_alert,
    _pipeline_run_context,
    reap_orphans,
    run_pipeline,
    send_telegram_summary,
)

BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678"
CHAT_ID = "-1001234567890"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from api.db import make_engine, override_engine

    # tiktok_uploader imports call load_dotenv(), so the developer's real .env
    # (which may set HOST to a live tunnel) would otherwise decide whether
    # these tests publish download links. Each test opts in explicitly.
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.setenv("MANUAL_DOWNLOAD_DIR", str(tmp_path / "store"))

    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))


def _make_pipeline(session=None, **overrides):
    now = now_utc()
    defaults = dict(
        name="no-account-pipe",
        channel_url="https://www.youtube.com/@x/videos",
        fetch_count=2,
        interval_value=6,
        interval_unit="hours",
        first_fire_at=now,
        next_fire_at=now,
        state="active",
        caption_template="{title}",
        upload_options_json="{}",
        account_ids_json="[]",
        telegram_bot_token=BOT_TOKEN,
        telegram_chat_id=CHAT_ID,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    if session is None:
        with Session(_api_db.engine) as s:
            s.add(p)
            s.commit()
            s.refresh(p)
            s.expunge(p)
        return p
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _fake_download(tmp_path, size_bytes=1024):
    """Return a youtube_channel.download stand-in writing a file per temp dir."""
    counter = {"n": 0}

    def _download(url, temp_dir, max_duration_seconds):
        counter["n"] += 1
        path = os.path.join(temp_dir, f"video{counter['n']}.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\0" * size_bytes)
        return path, "", ""

    return _download


class TestDownloadOnlyRun:
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    @patch("scheduler.pipeline_worker.tiktok_adapter.upload_from_options")
    def test_no_account_skips_upload(self, mock_upload, mock_download, mock_list, tmp_path):
        mock_list.return_value = [
            {"id": "v1", "title": "T1", "url": "https://www.youtube.com/watch?v=v1"},
            {"id": "v2", "title": "T2", "url": "https://www.youtube.com/watch?v=v2"},
        ]
        mock_download.side_effect = _fake_download(tmp_path)
        pipeline = _make_pipeline()
        artifacts = RunArtifacts()

        run = run_pipeline(pipeline, "scheduled", artifacts)

        assert run.uploaded_count == 0
        assert run.pulled_count == 2
        assert run.finished_at is not None
        mock_upload.assert_not_called()
        assert [v.source_url for v in artifacts.videos] == [
            "https://www.youtube.com/watch?v=v1",
            "https://www.youtube.com/watch?v=v2",
        ]
        assert [v.title for v in artifacts.videos] == ["T1", "T2"]
        # The caption is the deliverable on this path — it gets pasted by hand.
        assert [v.caption for v in artifacts.videos] == ["T1", "T2"]

    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    def test_second_tick_dedupes(self, mock_download, mock_list, tmp_path):
        mock_list.return_value = [
            {"id": "v1", "title": "T1", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.side_effect = _fake_download(tmp_path)
        pipeline = _make_pipeline()

        run_pipeline(pipeline, "scheduled", RunArtifacts())
        second = RunArtifacts()
        run2 = run_pipeline(pipeline, "scheduled", second)

        assert run2.pulled_count == 0
        assert run2.skipped_dup_count == 1
        assert second.videos == []
        with Session(_api_db.engine) as s:
            seen = s.exec(select(PipelineSeenVideo)).all()
            assert [r.source_key for r in seen] == ["v1"]


class TestSeenVideoRetention:
    @patch("scheduler.pipeline_worker.youtube_channel.list_top_n")
    @patch("scheduler.pipeline_worker.youtube_channel.download")
    def test_purge_keeps_seen_videos(self, mock_download, mock_list, tmp_path):
        """A video still in the channel's top-N must stay deduped forever."""
        from datetime import timedelta

        from scheduler.pipeline_worker import purge_old_runs

        mock_list.return_value = [
            {"id": "v1", "title": "T1", "url": "https://www.youtube.com/watch?v=v1"},
        ]
        mock_download.side_effect = _fake_download(tmp_path)
        pipeline = _make_pipeline()
        run_pipeline(pipeline, "scheduled", RunArtifacts())

        with Session(_api_db.engine) as s:
            seen = s.exec(select(PipelineSeenVideo)).first()
            seen.created_at = now_utc() - timedelta(days=365)
            s.add(seen)
            s.commit()

        purge_old_runs()

        with Session(_api_db.engine) as s:
            assert s.exec(select(PipelineSeenVideo)).all()

        second = RunArtifacts()
        assert run_pipeline(pipeline, "scheduled", second).skipped_dup_count == 1


class TestTelegramCredentialsBlock:
    def _pipeline_with_account(self, fields):
        with Session(_api_db.engine) as s:
            acct = Account(
                id=1,
                username="acc1",
                cookie_path="/tmp/acc1.cookie",
                login="acc1@example.com",
                login_password_encrypted=secrets.encrypt("s3cret"),
            )
            s.add(acct)
            s.commit()
        return _make_pipeline(
            account_ids_json="[1]",
            telegram_attach_fields_json=json.dumps(fields),
        )

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_credentials_rendered(self, mock_send):
        pipeline = self._pipeline_with_account(["login", "login_password"])
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled")

        send_telegram_summary(pipeline, run)

        text = mock_send.call_args[0][2]
        assert "🔑 acc1" in text
        assert "acc1@example.com / s3cret" in text

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_block_omitted_without_accounts(self, mock_send):
        pipeline = _make_pipeline(
            telegram_attach_fields_json=json.dumps(["login", "login_password"]),
        )
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled")

        send_telegram_summary(pipeline, run)

        assert "🔑" not in mock_send.call_args[0][2]

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_caption_and_source_listed_when_nothing_uploaded(self, mock_send, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\0" * 8)
        pipeline = _make_pipeline()
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled", pulled_count=1)
        artifacts = RunArtifacts(videos=[RunVideo(
            path=str(video),
            source_url="https://www.youtube.com/watch?v=v1",
            title="Clip one",
            caption="Clip one #tag",
        )])

        send_telegram_summary(pipeline, run, artifacts)

        text = mock_send.call_args[0][2]
        assert "Clip one" in text
        assert "<code>Clip one #tag</code>" in text
        assert "https://www.youtube.com/watch?v=v1" in text


class TestVideoAttachment:
    def _run_with(self, tmp_path, sizes):
        paths = []
        for i, size in enumerate(sizes):
            p = tmp_path / f"v{i}.mp4"
            p.write_bytes(b"\0" * size)
            paths.append(str(p))
        pipeline = _make_pipeline(telegram_attach_video=True)
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled")
        artifacts = RunArtifacts(videos=[
            RunVideo(path=p, source_url=f"https://src/{i}", title=f"v{i}")
            for i, p in enumerate(paths)
        ])
        return pipeline, run, artifacts

    @patch("scheduler.pipeline_worker.telegram.send_media_group")
    @patch("scheduler.pipeline_worker.telegram.send_document")
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_small_total_uses_media_group(self, _msg, mock_doc, mock_group, tmp_path):
        pipeline, run, artifacts = self._run_with(tmp_path, [1024, 2048])

        send_telegram_summary(pipeline, run, artifacts)

        mock_group.assert_called_once()
        mock_doc.assert_not_called()
        assert len(mock_group.call_args[0][2]) == 2

    @patch("scheduler.pipeline_worker.telegram.send_media_group")
    @patch("scheduler.pipeline_worker.telegram.send_document")
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_over_limit_total_sends_first_document(self, _msg, mock_doc, mock_group, tmp_path):
        with patch("scheduler.pipeline_worker.telegram.TELEGRAM_MAX_UPLOAD_BYTES", 4096):
            pipeline, run, artifacts = self._run_with(tmp_path, [2048, 3072])
            send_telegram_summary(pipeline, run, artifacts)

        mock_group.assert_not_called()
        mock_doc.assert_called_once()
        assert mock_doc.call_args[0][2] == artifacts.videos[0].path

    @patch("scheduler.pipeline_worker.telegram.send_media_group")
    @patch("scheduler.pipeline_worker.telegram.send_document")
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_single_file_over_limit_stays_text_only(self, _msg, mock_doc, mock_group, tmp_path):
        with patch("scheduler.pipeline_worker.telegram.TELEGRAM_MAX_UPLOAD_BYTES", 1024):
            pipeline, run, artifacts = self._run_with(tmp_path, [4096])
            send_telegram_summary(pipeline, run, artifacts)

        mock_group.assert_not_called()
        mock_doc.assert_not_called()

    @patch("scheduler.pipeline_worker.telegram.send_media_group")
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_attach_disabled_sends_text_only(self, mock_msg, mock_group, tmp_path):
        pipeline, run, artifacts = self._run_with(tmp_path, [1024])
        pipeline.telegram_attach_video = False

        send_telegram_summary(pipeline, run, artifacts)

        mock_msg.assert_called_once()
        mock_group.assert_not_called()


class TestVideoLinks:
    """Every notification carries a download link: the message costs nothing
    and the bytes only move when the operator clicks. It is also the only way
    past Telegram's 50 MB bot-upload cap."""

    def _pipeline_with_video(self, tmp_path, monkeypatch, base_url="https://tunnel.test"):
        monkeypatch.setenv("MANUAL_DOWNLOAD_DIR", str(tmp_path / "store"))
        if base_url:
            monkeypatch.setenv("PUBLIC_BASE_URL", base_url)
        else:
            monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
            monkeypatch.delenv("HOST", raising=False)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\0" * 32)
        pipeline = _make_pipeline()
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled", pulled_count=1)
        artifacts = RunArtifacts(videos=[RunVideo(
            path=str(video),
            source_url="https://www.youtube.com/watch?v=v1",
            title="Clip one",
        )])
        return pipeline, run, artifacts, video

    @patch("scheduler.pipeline_worker.telegram.send_media_group")
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_message_carries_signed_link(self, mock_send, mock_group, tmp_path, monkeypatch):
        from api.models import ManualDownload
        from api.services import manual_download

        pipeline, run, artifacts, video = self._pipeline_with_video(tmp_path, monkeypatch)

        send_telegram_summary(pipeline, run, artifacts)

        with Session(_api_db.engine) as s:
            row = s.exec(select(ManualDownload)).one()
        assert row.origin == "pipeline"
        assert row.title == "Clip one"
        assert row.pipeline_id == pipeline.id
        assert row.status == "ready"

        text = mock_send.call_args[0][2]
        expected = "https://tunnel.test" + manual_download.signed_path(
            row.id, manual_download.sign_id(row.id)
        )
        assert expected in text
        # The link replaces the upload; nothing is attached.
        mock_group.assert_not_called()
        # File moved into the store, not left in the run's temp dir.
        assert not video.exists()
        assert os.path.exists(row.file_path)

    @patch("scheduler.pipeline_worker.telegram.send_media_group")
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_attach_uploads_the_published_file(
        self, mock_send, mock_group, tmp_path, monkeypatch
    ):
        """Publishing moves the file; the upload has to follow it there."""
        pipeline, run, artifacts, video = self._pipeline_with_video(tmp_path, monkeypatch)
        pipeline.telegram_attach_video = True

        send_telegram_summary(pipeline, run, artifacts)

        mock_group.assert_called_once()
        uploaded = mock_group.call_args[0][2]
        assert len(uploaded) == 1
        assert uploaded[0] != str(video)
        assert os.path.getsize(uploaded[0]) == 32

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_no_public_url_degrades_to_text(self, mock_send, tmp_path, monkeypatch):
        from api.models import ManualDownload

        pipeline, run, artifacts, video = self._pipeline_with_video(
            tmp_path, monkeypatch, base_url=""
        )

        send_telegram_summary(pipeline, run, artifacts)

        assert "Tải video" not in mock_send.call_args[0][2]
        with Session(_api_db.engine) as s:
            assert s.exec(select(ManualDownload)).all() == []
        # Nothing was moved, so the normal cleanup still owns the file.
        assert video.exists()


class TestNotifyMode:
    """A window polls many times waiting for the author to post. Notifying on
    each poll is noise; never notifying makes a dead scheduler look like a
    quiet channel."""

    def _idle_run(self, pipeline):
        return PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                           trigger_reason="scheduled", skipped_dup_count=2)

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_cycle_mode_stays_quiet_mid_window(self, mock_send):
        pipeline = _make_pipeline(telegram_notify_mode="cycle")

        send_telegram_summary(pipeline, self._idle_run(pipeline), cycle_closed=False)

        mock_send.assert_not_called()

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_cycle_mode_reports_when_window_closes(self, mock_send):
        pipeline = _make_pipeline(telegram_notify_mode="cycle")

        send_telegram_summary(pipeline, self._idle_run(pipeline), cycle_closed=True)

        assert "Không có video mới" in mock_send.call_args[0][2]

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_changes_only_stays_quiet_at_cycle_end(self, mock_send):
        pipeline = _make_pipeline(telegram_notify_mode="changes_only")

        send_telegram_summary(pipeline, self._idle_run(pipeline), cycle_closed=True)

        mock_send.assert_not_called()

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_every_tick_reports_mid_window(self, mock_send):
        pipeline = _make_pipeline(telegram_notify_mode="every_tick")

        send_telegram_summary(pipeline, self._idle_run(pipeline), cycle_closed=False)

        mock_send.assert_called_once()

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_errors_ignore_the_mode(self, mock_send):
        """Silence is for quiet runs, never for failures."""
        pipeline = _make_pipeline(telegram_notify_mode="changes_only")
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled", error_text="low_disk")

        send_telegram_summary(pipeline, run, cycle_closed=False)

        assert "hết dung lượng đĩa" in mock_send.call_args[0][2]


class TestMessageEscaping:
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_title_and_caption_are_escaped(self, mock_send, tmp_path):
        """parse_mode=HTML means an unescaped < in a title breaks the send."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\0" * 8)
        pipeline = _make_pipeline(name="A & B")
        run = PipelineRun(id=1, pipeline_id=pipeline.id, started_at=now_utc(),
                          trigger_reason="scheduled", pulled_count=1)
        artifacts = RunArtifacts(videos=[RunVideo(
            path=str(video),
            source_url="https://example.test/v1",
            title="<b>hack</b> & run",
            caption="caption <script>",
        )])

        send_telegram_summary(pipeline, run, artifacts)

        text = mock_send.call_args[0][2]
        assert "&lt;b&gt;hack&lt;/b&gt; &amp; run" in text
        assert "caption &lt;script&gt;" in text
        assert "A &amp; B" in text
        assert mock_send.call_args[1]["html"] is True


class TestDeletionOrdering:
    def _pipeline_and_dir(self, tmp_path):
        d = tmp_path / "dl"
        d.mkdir()
        (d / "v.mp4").write_bytes(b"\0" * 16)
        return _make_pipeline(telegram_attach_video=True), str(d)

    @patch("scheduler.pipeline_worker.send_telegram_summary")
    def test_files_survive_until_notification_sent(self, mock_notify, tmp_path):
        pipeline, d = self._pipeline_and_dir(tmp_path)
        seen = {}

        def _record(_pipeline, _run, artifacts=None, cycle_closed=True):
            seen["exists"] = os.path.exists(d)

        mock_notify.side_effect = _record

        def _fake_run(_pipeline, _reason, artifacts=None):
            artifacts.cleanup_paths.append(d)
            return PipelineRun(id=1, pipeline_id=pipeline.id,
                               started_at=now_utc(), trigger_reason="scheduled")

        with patch("scheduler.pipeline_worker.run_pipeline", side_effect=_fake_run):
            with _pipeline_run_context(pipeline, "scheduled"):
                pass

        assert seen["exists"] is True
        assert not os.path.exists(d)

    @patch("scheduler.pipeline_worker.send_telegram_summary", side_effect=RuntimeError("boom"))
    def test_files_deleted_even_when_notification_raises(self, _notify, tmp_path):
        pipeline, d = self._pipeline_and_dir(tmp_path)

        def _fake_run(_pipeline, _reason, artifacts=None):
            artifacts.cleanup_paths.append(d)
            return PipelineRun(id=1, pipeline_id=pipeline.id,
                               started_at=now_utc(), trigger_reason="scheduled")

        with patch("scheduler.pipeline_worker.run_pipeline", side_effect=_fake_run):
            with _pipeline_run_context(pipeline, "scheduled"):
                pass

        assert not os.path.exists(d)

    @patch("scheduler.pipeline_worker._notify_pipeline_alert")
    @patch("scheduler.pipeline_worker.send_telegram_summary")
    @patch("scheduler.pipeline_worker._emergency_finalize")
    def test_crash_deletes_files_and_alerts(self, _fin, mock_notify, mock_alert, tmp_path):
        pipeline, d = self._pipeline_and_dir(tmp_path)

        def _fake_run(_pipeline, _reason, artifacts=None):
            artifacts.cleanup_paths.append(d)
            raise RuntimeError("crash")

        with patch("scheduler.pipeline_worker.run_pipeline", side_effect=_fake_run):
            with pytest.raises(RuntimeError):
                with _pipeline_run_context(pipeline, "scheduled"):
                    pass

        assert not os.path.exists(d)
        mock_alert.assert_called_once()


class TestReaperAlert:
    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_reaped_run_alerts(self, mock_send):
        from datetime import timedelta

        pipeline = _make_pipeline()
        with Session(_api_db.engine) as s:
            s.add(PipelineRun(
                pipeline_id=pipeline.id,
                started_at=now_utc() - timedelta(minutes=30),
                trigger_reason="scheduled",
            ))
            s.commit()

        reap_orphans()

        mock_send.assert_called_once()
        assert "reaped" in mock_send.call_args[0][2]

    @patch("scheduler.pipeline_worker.telegram.send_message")
    def test_deleted_pipeline_does_not_raise(self, mock_send):
        """A pipeline removed between the reap and the alert is a no-op."""
        _notify_pipeline_alert(999999, "⚠️ anything")

        mock_send.assert_not_called()
