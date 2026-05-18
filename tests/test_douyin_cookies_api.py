"""Tests for GET/POST/PUT/DELETE /api/douyin-cookies endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import api.db as _api_db
from api.db import make_engine, override_engine
from api.main import app  # noqa: F401 — registers all models in SQLModel.metadata
from api.models import DouyinCookie  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from api.services import secrets as _sec
    _sec._reset_for_tests()
    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))
    _sec._reset_for_tests()


@pytest.fixture
def client():
    return TestClient(app, headers={"Origin": "http://127.0.0.1:3000"})


VALID_COOKIE_LIST = [
    {"name": "ttwid", "value": "abc123xyz"},
    {"name": "msToken", "value": "tok456"},
]


class TestListDouyinCookies:
    def test_empty_list(self, client):
        resp = client.get("/api/douyin-cookies")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_cookie_json_not_in_response(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        client.post(
            "/api/douyin-cookies",
            data={"name": "Main"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        resp = client.get("/api/douyin-cookies")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert "cookie_json_encrypted" not in rows[0]
        assert "id" in rows[0]
        assert "name" in rows[0]
        assert "created_at" in rows[0]


class TestCreateDouyinCookie:
    def test_create_valid(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Account 1"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Account 1"
        assert "id" in body
        assert "cookie_json_encrypted" not in body

    def test_create_missing_ttwid_returns_422(self, client):
        no_ttwid = [{"name": "msToken", "value": "tok"}]
        cookie_bytes = json.dumps(no_ttwid).encode()
        resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Bad"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert resp.status_code == 422

    def test_create_malformed_json_returns_422(self, client):
        resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Bad"},
            files={"file": ("cookies.json", b"not json", "application/json")},
        )
        assert resp.status_code == 422


class TestUpdateDouyinCookie:
    def test_update_cookie(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        create_resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Old"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert create_resp.status_code == 201
        cookie_id = create_resp.json()["id"]

        new_cookies = [{"name": "ttwid", "value": "newtoken999"}]
        new_bytes = json.dumps(new_cookies).encode()
        update_resp = client.put(
            f"/api/douyin-cookies/{cookie_id}",
            files={"file": ("cookies.json", new_bytes, "application/json")},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["id"] == cookie_id

    def test_update_nonexistent_returns_404(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        resp = client.put(
            "/api/douyin-cookies/9999",
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert resp.status_code == 404


class TestDeleteDouyinCookie:
    def test_delete_existing(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        create_resp = client.post(
            "/api/douyin-cookies",
            data={"name": "To delete"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        cookie_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/douyin-cookies/{cookie_id}")
        assert del_resp.status_code == 204

        list_resp = client.get("/api/douyin-cookies")
        assert list_resp.json() == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/douyin-cookies/9999")
        assert resp.status_code == 404
