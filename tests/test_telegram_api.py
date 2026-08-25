"""Tests for /api/telegram detect-chats and test endpoints."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from api.middleware.origin import LOCAL_ORIGIN

HEADERS_OK = {"Origin": LOCAL_ORIGIN}

VALID_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678"
VALID_CHAT = "-1001234567890"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    from tiktok_uploader.Config import Config

    Config._instance = None
    from api import db as _db
    from api.main import create_app

    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    from api.db_migrations import apply_indexes

    apply_indexes(eng)
    return TestClient(create_app())


class TestDetectChats:
    def test_invalid_token_422(self, client):
        r = client.post(
            "/api/telegram/detect-chats",
            json={"bot_token": "not-a-token"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    @patch("api.services.telegram.httpx.Client")
    def test_returns_chats_deduped(self, mock_cls, client):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        def post_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            u = str(url)
            if "getUpdates" in u:
                mock_resp.json.return_value = {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 1,
                            "channel_post": {
                                "chat": {
                                    "id": -1003735696189,
                                    "title": "NotifyTiktok",
                                    "type": "channel",
                                }
                            },
                        },
                        {
                            "update_id": 2,
                            "message": {
                                "chat": {
                                    "id": -1003735696189,
                                    "title": "NotifyTiktok",
                                    "type": "channel",
                                }
                            },
                        },
                    ],
                }
            return mock_resp

        mock_client.post.side_effect = post_side_effect

        r = client.post(
            "/api/telegram/detect-chats",
            json={"bot_token": VALID_TOKEN},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "chats": [
                {"id": -1003735696189, "title": "NotifyTiktok", "type": "channel"}
            ]
        }
        assert mock_client.post.call_count == 1

    @patch("api.services.telegram.httpx.Client")
    def test_empty_result_returns_502(self, mock_cls, client):
        """Bug 6: no recent messages → 502 (not empty 200) so frontend shows friendly error."""
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        def post_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": []}
            return mock_resp

        def get_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": {"url": ""}}
            return mock_resp

        mock_client.post.side_effect = post_side_effect
        mock_client.get.side_effect = get_side_effect

        r = client.post(
            "/api/telegram/detect-chats",
            json={"bot_token": VALID_TOKEN},
            headers=HEADERS_OK,
        )
        assert r.status_code == 502

    @patch("api.services.telegram.httpx.Client")
    def test_get_updates_api_error_502(self, mock_cls, client):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        def post_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "getUpdates" in str(url):
                mock_resp.json.return_value = {"ok": False, "description": "fail"}
            return mock_resp

        mock_client.post.side_effect = post_side_effect

        r = client.post(
            "/api/telegram/detect-chats",
            json={"bot_token": VALID_TOKEN},
            headers=HEADERS_OK,
        )
        assert r.status_code == 502


class TestTestEndpoint:
    @patch("api.routers.telegram.send_message")
    def test_success(self, mock_send, client):
        mock_send.return_value = True
        r = client.post(
            "/api/telegram/test",
            json={"bot_token": VALID_TOKEN, "chat_id": VALID_CHAT},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert "successfully" in body["message"]
        mock_send.assert_called_once()
        a0, a1, a2 = mock_send.call_args[0]
        assert a0 == VALID_TOKEN
        assert a1 == VALID_CHAT
        assert "TikTok Uploader" in a2

    @patch("api.routers.telegram.send_message")
    def test_send_failed_400(self, mock_send, client):
        mock_send.return_value = False
        r = client.post(
            "/api/telegram/test",
            json={"bot_token": VALID_TOKEN, "chat_id": VALID_CHAT},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400

    @patch("api.routers.telegram.send_message")
    def test_valueerror_from_send_400(self, mock_send, client):
        mock_send.side_effect = ValueError("telegram chat_id format invalid")
        r = client.post(
            "/api/telegram/test",
            json={"bot_token": VALID_TOKEN, "chat_id": VALID_CHAT},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400

    def test_invalid_chat_422(self, client):
        r = client.post(
            "/api/telegram/test",
            json={"bot_token": VALID_TOKEN, "chat_id": "@nope"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
