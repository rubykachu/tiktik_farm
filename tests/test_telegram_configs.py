"""Tests for GET/POST/DELETE /api/telegram-configs endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.db as _api_db
from api.db import make_engine, override_engine
from api.main import app  # noqa: F401 — registers all models in SQLModel.metadata
from api.models import TelegramConfig  # noqa: F401
from api.middleware.origin import LOCAL_ORIGIN

VALID_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678"
VALID_CHAT = "-1001234567890"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    _api_db.init_db()  # create_all + _run_migrations (matches production startup)
    yield engine
    override_engine(make_engine("sqlite:///"))


@pytest.fixture
def client():
    return TestClient(app, headers={"Origin": LOCAL_ORIGIN})


class TestListTelegramConfigs:
    def test_empty_list(self, client):
        resp = client.get("/api/telegram-configs")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCreateTelegramConfig:
    def test_create_valid(self, client):
        resp = client.post(
            "/api/telegram-configs",
            json={"name": "Main channel", "bot_token": VALID_TOKEN, "chat_id": VALID_CHAT},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Main channel"
        assert body["bot_token"] == VALID_TOKEN
        assert body["chat_id"] == VALID_CHAT
        assert "id" in body
        assert "created_at" in body

    def test_create_invalid_bot_token_returns_422(self, client):
        resp = client.post(
            "/api/telegram-configs",
            json={"name": "Bad", "bot_token": "not-a-token", "chat_id": VALID_CHAT},
        )
        assert resp.status_code == 422

    def test_create_invalid_chat_id_returns_422(self, client):
        resp = client.post(
            "/api/telegram-configs",
            json={"name": "Bad", "bot_token": VALID_TOKEN, "chat_id": "@nope"},
        )
        assert resp.status_code == 422

    def test_create_missing_name_returns_422(self, client):
        resp = client.post(
            "/api/telegram-configs",
            json={"name": "", "bot_token": VALID_TOKEN, "chat_id": VALID_CHAT},
        )
        assert resp.status_code == 422


class TestDeleteTelegramConfig:
    def test_delete_existing(self, client):
        create_resp = client.post(
            "/api/telegram-configs",
            json={"name": "To delete", "bot_token": VALID_TOKEN, "chat_id": VALID_CHAT},
        )
        config_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/telegram-configs/{config_id}")
        assert del_resp.status_code == 204

        list_resp = client.get("/api/telegram-configs")
        assert list_resp.json() == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/telegram-configs/9999")
        assert resp.status_code == 404
