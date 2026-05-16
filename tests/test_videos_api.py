"""Tests for GET/DELETE /api/videos endpoints."""
import pytest
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import api.db as _api_db
from api.db import make_engine, override_engine, now_utc
from api.main import app
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))


@pytest.fixture
def client():
    return TestClient(app, headers={"Origin": "http://127.0.0.1:3000"})


def _seed_data():
    """Create a pipeline, run, account, and a few VideoUpload rows."""
    now = now_utc()
    with Session(_api_db.engine) as s:
        acct = Account(id=1, username="user1", cookie_path="/tmp/c", has_valid_session=True)
        s.add(acct)

        p = ChannelPipeline(
            name="p1",
            channel_url="https://www.youtube.com/@test/videos",
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
        s.add(p)
        s.commit()
        s.refresh(p)

        run = PipelineRun(
            pipeline_id=p.id,
            started_at=now - timedelta(minutes=5),
            finished_at=now,
            trigger_reason="scheduled",
            pulled_count=2,
            uploaded_count=1,
            failed_count=1,
        )
        s.add(run)
        s.commit()
        s.refresh(run)

        v1 = VideoUpload(
            pipeline_id=p.id,
            run_id=run.id,
            account_id=1,
            youtube_video_id="abc123",
            youtube_url="https://youtube.com/watch?v=abc123",
            youtube_title="Test Video",
            status="succeeded",
            tiktok_video_id="tk001",
            tiktok_url="https://tiktok.com/@user/video/tk001",
            rendered_caption="Hello #test",
            attempt_count=1,
            created_at=now - timedelta(minutes=3),
        )
        v2 = VideoUpload(
            pipeline_id=p.id,
            run_id=run.id,
            account_id=1,
            youtube_video_id="def456",
            youtube_url="https://youtube.com/watch?v=def456",
            youtube_title="Test Video 2",
            status="failed",
            error_text="upload returned false",
            attempt_count=2,
            created_at=now - timedelta(minutes=1),
        )
        s.add_all([v1, v2])
        s.commit()
        return {"pipeline_id": p.id, "run_id": run.id, "video_ids": [v1.id, v2.id]}


class TestListVideos:
    def test_empty(self, client):
        resp = client.get("/api/videos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all(self, client):
        _seed_data()
        resp = client.get("/api/videos")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_filter_by_status_succeeded(self, client):
        _seed_data()
        resp = client.get("/api/videos", params={"status": "succeeded"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "succeeded"

    def test_filter_by_status_failed(self, client):
        _seed_data()
        resp = client.get("/api/videos", params={"status": "failed"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "failed"

    def test_invalid_status_422(self, client):
        _seed_data()
        resp = client.get("/api/videos", params={"status": "bogus"})
        assert resp.status_code == 422

    def test_filter_by_account(self, client):
        _seed_data()
        resp = client.get("/api/videos", params={"account_id": "1"})
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        resp = client.get("/api/videos", params={"account_id": "999"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_invalid_from_date_422(self, client):
        _seed_data()
        resp = client.get("/api/videos", params={"from_date": "not-a-date"})
        assert resp.status_code == 422

    def test_pagination_caps_size(self, client):
        resp = client.get("/api/videos", params={"size": "999"})
        assert resp.status_code == 422

    def test_pagination_rejects_page_zero(self, client):
        resp = client.get("/api/videos", params={"page": "0"})
        assert resp.status_code == 422


class TestDeleteVideo:
    def test_delete_success(self, client):
        ids = _seed_data()
        vid = ids["video_ids"][0]
        resp = client.delete(f"/api/videos/{vid}")
        assert resp.status_code == 204

        resp = client.get("/api/videos")
        assert len(resp.json()) == 1

    def test_delete_not_found(self, client):
        resp = client.delete("/api/videos/999")
        assert resp.status_code == 404
