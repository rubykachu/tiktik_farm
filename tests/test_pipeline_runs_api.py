"""Tests for GET /api/pipelines/{id}/runs and /runs/{id}/videos endpoints."""
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


def _seed():
    now = now_utc()
    with Session(_api_db.engine) as s:
        acct = Account(id=1, username="user1", cookie_path="/tmp/c", has_valid_session=True)
        s.add(acct)

        p = ChannelPipeline(
            name="pipe1",
            channel_url="https://www.youtube.com/@ch/videos",
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
            started_at=now - timedelta(minutes=10),
            finished_at=now,
            trigger_reason="manual",
            pulled_count=3,
            uploaded_count=2,
            failed_count=1,
        )
        s.add(run)
        s.commit()
        s.refresh(run)

        v = VideoUpload(
            pipeline_id=p.id,
            run_id=run.id,
            account_id=1,
            youtube_video_id="vid1",
            youtube_url="https://youtube.com/watch?v=vid1",
            source_title="Title 1",
            status="succeeded",
            attempt_count=1,
            created_at=now,
        )
        s.add(v)
        s.commit()
        return {"pipeline_id": p.id, "run_id": run.id, "video_id": v.id}


class TestListRuns:
    def test_happy(self, client):
        ids = _seed()
        resp = client.get(f"/api/pipelines/{ids['pipeline_id']}/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["trigger_reason"] == "manual"
        assert data[0]["pulled_count"] == 3

    def test_empty_runs(self, client):
        ids = _seed()
        with Session(_api_db.engine) as s:
            p2 = ChannelPipeline(
                name="empty",
                channel_url="https://www.youtube.com/@empty/videos",
                fetch_count=1,
                interval_value=1,
                interval_unit="hours",
                first_fire_at=now_utc(),
                next_fire_at=now_utc(),
                state="active",
                caption_template="{title}",
                upload_options_json="{}",
                account_ids_json="[1]",
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            s.add(p2)
            s.commit()
            s.refresh(p2)
            p2_id = p2.id

        resp = client.get(f"/api/pipelines/{p2_id}/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pipeline_not_found(self, client):
        resp = client.get("/api/pipelines/999/runs")
        assert resp.status_code == 404

    def test_pagination_rejects_invalid(self, client):
        ids = _seed()
        resp = client.get(f"/api/pipelines/{ids['pipeline_id']}/runs", params={"page": "0"})
        assert resp.status_code == 422

        resp = client.get(f"/api/pipelines/{ids['pipeline_id']}/runs", params={"size": "999"})
        assert resp.status_code == 422


class TestListRunVideos:
    def test_happy(self, client):
        ids = _seed()
        resp = client.get(
            f"/api/pipelines/{ids['pipeline_id']}/runs/{ids['run_id']}/videos"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["youtube_video_id"] == "vid1"
        assert data[0]["status"] == "succeeded"

    def test_pipeline_not_found(self, client):
        resp = client.get("/api/pipelines/999/runs/1/videos")
        assert resp.status_code == 404

    def test_run_not_found(self, client):
        ids = _seed()
        resp = client.get(f"/api/pipelines/{ids['pipeline_id']}/runs/999/videos")
        assert resp.status_code == 404

    def test_run_wrong_pipeline(self, client):
        ids = _seed()
        now = now_utc()
        with Session(_api_db.engine) as s:
            p2 = ChannelPipeline(
                name="other",
                channel_url="https://www.youtube.com/@other/videos",
                fetch_count=1,
                interval_value=1,
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
            s.add(p2)
            s.commit()
            s.refresh(p2)
            p2_id = p2.id

        # run belongs to pipeline 1, accessing via pipeline 2 → 404
        resp = client.get(f"/api/pipelines/{p2_id}/runs/{ids['run_id']}/videos")
        assert resp.status_code == 404
