"""Tests for POST /api/pipelines/:id/run-now endpoint."""
import pytest
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import api.db as _api_db
from api.db import make_engine, override_engine, now_utc
from api.main import app
from api.models import ChannelPipeline


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


def _make_pipeline(**overrides) -> int:
    now = now_utc()
    defaults = dict(
        name="test",
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
    defaults.update(overrides)
    p = ChannelPipeline(**defaults)
    with Session(_api_db.engine) as s:
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


class TestRunNow:
    def test_happy_202(self, client):
        pid = _make_pipeline()
        resp = client.post(f"/api/pipelines/{pid}/run-now")
        assert resp.status_code == 202
        assert resp.json()["ok"] is True

        with Session(_api_db.engine) as s:
            p = s.get(ChannelPipeline, pid)
            assert p.run_now_requested_at is not None

    def test_409_pipeline_running(self, client):
        now = now_utc()
        pid = _make_pipeline(lock_until=now + timedelta(minutes=5))
        resp = client.post(f"/api/pipelines/{pid}/run-now")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "PIPELINE_RUNNING"

    def test_409_already_queued(self, client):
        now = now_utc()
        pid = _make_pipeline(run_now_requested_at=now)
        resp = client.post(f"/api/pipelines/{pid}/run-now")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "RUN_NOW_QUEUED"

    def test_404_not_found(self, client):
        resp = client.post("/api/pipelines/999/run-now")
        assert resp.status_code == 404
