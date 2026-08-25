"""Tests for /api/overview — the numbers the dashboard states outright."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

HEADERS_OK = {"Origin": "http://127.0.0.1:3000"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    monkeypatch.setenv("MANUAL_DOWNLOAD_DIR", str(tmp_path / "manual_downloads"))
    from tiktok_uploader.Config import Config
    Config._instance = None
    from api import db as _db
    from api.db_migrations import apply_indexes, apply_schema_patches
    from api.main import create_app

    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    apply_schema_patches(eng)
    apply_indexes(eng)
    return TestClient(create_app())


def _seed(**kw):
    from api import db as _db
    from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload

    now = _now()
    with Session(_db.engine) as s:
        acct = Account(id=1, username="acc1", cookie_path="/x/acc1.cookie")
        s.add(acct)
        if kw.get("invalid_account"):
            s.add(Account(id=2, username="broken", cookie_path="/x/b.cookie",
                          has_valid_session=False))

        pipeline = ChannelPipeline(
            name="p1",
            channel_url="https://www.youtube.com/@x/videos",
            fetch_count=1,
            interval_value=1,
            interval_unit="hours",
            first_fire_at=now,
            next_fire_at=now + timedelta(hours=2),
            state=kw.get("pipeline_state", "active"),
            caption_template="{title}",
            upload_options_json="{}",
            account_ids_json="[1]",
        )
        s.add(pipeline)
        s.commit()
        s.refresh(pipeline)

        for status, age_hours in kw.get("uploads", []):
            s.add(VideoUpload(
                pipeline_id=pipeline.id,
                account_id=1,
                youtube_video_id="v",
                youtube_url="https://youtu.be/v",
                source_title="A clip",
                status=status,
                error_text="cookie expired" if status == "failed" else None,
                created_at=now - timedelta(hours=age_hours),
            ))

        if kw.get("stuck_run"):
            s.add(PipelineRun(
                pipeline_id=pipeline.id,
                started_at=now - timedelta(hours=1),
                trigger_reason="scheduled",
            ))
        s.commit()
        return pipeline.id


class TestOverview:
    def test_empty_install_reports_zeroes(self, client):
        body = client.get("/api/overview").json()
        assert body["uploads_24h"] == {"succeeded": 0, "failed": 0}
        assert body["pipelines"] == {"active": 0, "total": 0}
        assert body["next_fire_at"] is None
        assert body["recent_errors"] == []

    def test_counts_only_the_last_24h(self, client):
        _seed(uploads=[("succeeded", 1), ("failed", 2), ("succeeded", 30)])

        body = client.get("/api/overview").json()

        # The 30h-old upload must not inflate today's numbers.
        assert body["uploads_24h"] == {"succeeded": 1, "failed": 1}

    def test_recent_errors_carry_what_broke(self, client):
        _seed(uploads=[("failed", 1)])

        errors = client.get("/api/overview").json()["recent_errors"]

        assert len(errors) == 1
        assert errors[0]["error"] == "cookie expired"
        assert errors[0]["title"] == "A clip"

    def test_paused_pipeline_counts_in_total_not_active(self, client):
        _seed(pipeline_state="paused")

        body = client.get("/api/overview").json()

        assert body["pipelines"] == {"active": 0, "total": 1}
        # A paused pipeline has no upcoming fire to report.
        assert body["next_fire_at"] is None

    def test_next_fire_comes_from_active_pipelines(self, client):
        _seed()

        assert client.get("/api/overview").json()["next_fire_at"] is not None

    def test_invalid_account_is_surfaced_by_name(self, client):
        _seed(invalid_account=True)

        accounts = client.get("/api/overview").json()["accounts"]

        assert accounts["total"] == 2
        assert accounts["invalid"] == 1
        assert accounts["invalid_usernames"] == ["broken"]

    def test_stuck_run_is_flagged(self, client):
        """An unfinished run older than the stale threshold means the
        scheduler died mid-tick — the operator has no other way to see it."""
        _seed(stuck_run=True)

        assert client.get("/api/overview").json()["stuck_runs"] == 1

    def test_ready_downloads_counted(self, client):
        from unittest.mock import patch

        from api.services.manual_download import DownloadedVideo

        def _stub(url, output_dir):
            import os
            os.makedirs(output_dir, exist_ok=True)
            path = os.path.join(output_dir, "clip.mp4")
            with open(path, "wb") as fh:
                fh.write(b"x")
            return DownloadedVideo(file_path=path, title="t", description="",
                                   duration_seconds=1, file_size=1)

        with patch("api.services.manual_download.download", side_effect=_stub):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)

        assert client.get("/api/overview").json()["downloads_ready"] == 1
