"""Tests for Douyin pipeline source_type validation (Task 6).

- source_type="douyin" requires douyin_cookie_id
- source_type="youtube" requires no douyin_cookie_id
- channel_url YouTube-domain check only fires for source_type="youtube"
"""
from __future__ import annotations

import json
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
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from tiktok_uploader.Config import Config
    Config._instance = None
    from api import db as _db
    from api.main import create_app
    from api.services import secrets as _sec
    _sec._reset_for_tests()
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    from api.db_migrations import apply_indexes, apply_schema_patches
    apply_schema_patches(eng)
    apply_indexes(eng)
    yield TestClient(create_app(), headers=HEADERS_OK)
    _sec._reset_for_tests()


def _make_account(client) -> int:
    from sqlmodel import Session
    from api import db as _db
    from api.models import Account
    with Session(_db.engine) as s:
        a = Account(username="user1", cookie_path="/x/user1.cookie")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _make_douyin_cookie(client) -> int:
    cookie_bytes = json.dumps([{"name": "ttwid", "value": "abc123"}]).encode()
    resp = client.post(
        "/api/douyin-cookies",
        data={"name": "Cookie 1"},
        files={"file": ("cookies.json", cookie_bytes, "application/json")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _base_payload(account_id: int, **overrides):
    base = {
        "name": "Test Pipeline",
        "channel_url": "https://youtube.com/@test/videos",
        "fetch_count": 3,
        "interval_value": 1,
        "interval_unit": "hours",
        "first_fire_at": (_now() + timedelta(hours=1)).isoformat(),
        "caption_template": "{title}",
        "account_ids": [account_id],
    }
    base.update(overrides)
    return base


class TestDouyinPipelineCreate:
    def test_douyin_with_cookie_id_succeeds(self, client):
        account_id = _make_account(client)
        cookie_id = _make_douyin_cookie(client)
        payload = _base_payload(
            account_id,
            source_type="douyin",
            douyin_cookie_id=cookie_id,
            channel_url="https://www.douyin.com/user/MS4wLjAB",
        )
        resp = client.post("/api/pipelines", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["source_type"] == "douyin"
        assert body["douyin_cookie_id"] == cookie_id

    def test_douyin_without_cookie_id_returns_422(self, client):
        account_id = _make_account(client)
        payload = _base_payload(
            account_id,
            source_type="douyin",
            channel_url="https://www.douyin.com/user/MS4wLjAB",
        )
        resp = client.post("/api/pipelines", json=payload)
        assert resp.status_code == 422

    def test_douyin_url_not_validated_as_youtube(self, client):
        """Douyin channel URL should not be rejected by YouTube domain check."""
        account_id = _make_account(client)
        cookie_id = _make_douyin_cookie(client)
        payload = _base_payload(
            account_id,
            source_type="douyin",
            douyin_cookie_id=cookie_id,
            channel_url="https://www.douyin.com/user/MS4wLjAB",
        )
        resp = client.post("/api/pipelines", json=payload)
        assert resp.status_code == 201

    def test_youtube_with_cookie_id_returns_422(self, client):
        """YouTube pipeline must not set douyin_cookie_id."""
        account_id = _make_account(client)
        cookie_id = _make_douyin_cookie(client)
        payload = _base_payload(
            account_id,
            source_type="youtube",
            douyin_cookie_id=cookie_id,
        )
        resp = client.post("/api/pipelines", json=payload)
        assert resp.status_code == 422

    def test_youtube_url_check_still_active(self, client):
        """YouTube channel URL must still be validated for source_type=youtube."""
        account_id = _make_account(client)
        payload = _base_payload(
            account_id,
            channel_url="https://evil.com/@x/videos",
        )
        resp = client.post("/api/pipelines", json=payload)
        assert resp.status_code == 422

    def test_default_source_type_is_youtube(self, client):
        account_id = _make_account(client)
        payload = _base_payload(account_id)
        resp = client.post("/api/pipelines", json=payload)
        assert resp.status_code == 201
        assert resp.json()["source_type"] == "youtube"


class TestDouyinPipelineUpdate:
    def test_patch_douyin_cookie_id(self, client):
        account_id = _make_account(client)
        cookie_id = _make_douyin_cookie(client)
        # Create a Douyin pipeline
        payload = _base_payload(
            account_id,
            source_type="douyin",
            douyin_cookie_id=cookie_id,
            channel_url="https://www.douyin.com/user/MS4wLjAB",
        )
        create_resp = client.post("/api/pipelines", json=payload)
        assert create_resp.status_code == 201
        pid = create_resp.json()["id"]

        cookie_id2 = _make_douyin_cookie(client)
        patch_resp = client.patch(f"/api/pipelines/{pid}", json={"douyin_cookie_id": cookie_id2})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["douyin_cookie_id"] == cookie_id2

    def test_patch_youtube_cannot_set_cookie_id(self, client):
        account_id = _make_account(client)
        cookie_id = _make_douyin_cookie(client)
        payload = _base_payload(account_id)
        create_resp = client.post("/api/pipelines", json=payload)
        assert create_resp.status_code == 201
        pid = create_resp.json()["id"]

        patch_resp = client.patch(
            f"/api/pipelines/{pid}", json={"douyin_cookie_id": cookie_id}
        )
        assert patch_resp.status_code == 422
