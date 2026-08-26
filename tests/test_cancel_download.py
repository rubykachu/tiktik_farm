"""Tests for POST /api/pipelines/:id/cancel-download endpoint."""
import pytest
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import api.db as _api_db
from api.db import make_engine, override_engine, now_utc
from api.main import app
from api.models import ChannelPipeline
from api.middleware.origin import LOCAL_ORIGIN


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
    return TestClient(app, headers={"Origin": LOCAL_ORIGIN})


def _make_pipeline(**overrides) -> int:
    now = now_utc()
    defaults = dict(
        name="test",
        channel_url="https://www.youtube.com/@test/videos",
        source_type="youtube",
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


class TestCancelDownload:
    def test_happy_202(self, client):
        now = now_utc()
        pid = _make_pipeline(lock_until=now + timedelta(minutes=5))
        resp = client.post(f"/api/pipelines/{pid}/cancel-download")
        assert resp.status_code == 202
        assert resp.json()["ok"] is True

        with Session(_api_db.engine) as s:
            p = s.get(ChannelPipeline, pid)
            assert p.cancel_requested_at is not None

    def test_404_not_found(self, client):
        resp = client.post("/api/pipelines/999/cancel-download")
        assert resp.status_code == 404

    def test_409_not_running(self, client):
        pid = _make_pipeline()  # no lock_until
        resp = client.post(f"/api/pipelines/{pid}/cancel-download")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NOT_RUNNING"

    def test_409_lock_expired(self, client):
        now = now_utc()
        pid = _make_pipeline(lock_until=now - timedelta(minutes=1))
        resp = client.post(f"/api/pipelines/{pid}/cancel-download")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "NOT_RUNNING"

    def test_422_douyin_not_supported(self, client):
        now = now_utc()
        pid = _make_pipeline(
            source_type="douyin",
            channel_url="https://www.douyin.com/user/x",
            lock_until=now + timedelta(minutes=5),
        )
        resp = client.post(f"/api/pipelines/{pid}/cancel-download")
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "NOT_SUPPORTED"
