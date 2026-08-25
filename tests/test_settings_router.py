"""Tests for /api/settings and /api/llm-models routers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from api.middleware.origin import LOCAL_ORIGIN

HEADERS_OK = {"Origin": LOCAL_ORIGIN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    from tiktok_uploader.Config import Config
    Config._instance = None

    from api import db as _db
    from api.services import secrets as _secrets
    _secrets._reset_for_tests()

    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    from api.db_migrations import apply_indexes, apply_schema_patches, seed_app_settings
    apply_schema_patches(eng)
    apply_indexes(eng)
    seed_app_settings(eng)

    from api.main import create_app
    yield TestClient(create_app())
    _secrets._reset_for_tests()


class TestGetSettings:
    def test_default_enabled_true(self, client):
        r = client.get("/api/settings", headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json()["llm_enabled"] is True


class TestPatchSettings:
    def test_can_disable(self, client):
        r = client.patch(
            "/api/settings",
            json={"llm_enabled": False},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json()["llm_enabled"] is False
        r2 = client.get("/api/settings", headers=HEADERS_OK)
        assert r2.json()["llm_enabled"] is False

    def test_partial_patch_keeps_unchanged_fields(self, client):
        r = client.patch("/api/settings", json={}, headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json()["llm_enabled"] is True


class TestTestLLMConnection:
    @patch("api.routers.settings.llm.test_completion")
    def test_success(self, mock_test, client):
        mock_test.return_value = (True, "ok")
        r = client.post(
            "/api/settings/llm/test",
            json={"model_id": "gemini/x", "api_key": "sk-test-key-1234"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "message": "ok"}
        # Encrypted blob, never plaintext.
        kw = mock_test.call_args.kwargs
        assert kw["model_id"] == "gemini/x"
        assert kw["api_key_encrypted"] != "sk-test-key-1234"
        assert kw["api_key_encrypted"]  # truthy

    @patch("api.routers.settings.llm.test_completion")
    def test_failure(self, mock_test, client):
        mock_test.return_value = (False, "bad key")
        r = client.post(
            "/api/settings/llm/test",
            json={"model_id": "openai/x", "api_key": "x"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json() == {"ok": False, "message": "bad key"}


class TestLLMModelsCRUD:
    def test_list_empty_initially(self, client):
        r = client.get("/api/llm-models", headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json() == []

    def test_create_returns_masked_key(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "Gemini Flash Lite",
                "model_id": "gemini/gemini-2.0-flash-lite",
                "api_key": "AIzaSy-VERYLONG-FAKE-KEY-12345",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["label"] == "Gemini Flash Lite"
        assert body["model_id"] == "gemini/gemini-2.0-flash-lite"
        assert "api_key" not in body
        assert "•" in body["api_key_masked"]
        assert body["api_key_masked"].startswith("AIza")
        assert body["api_key_masked"].endswith("2345")
        assert "FAKE-KEY-12345" not in body["api_key_masked"]
        assert body["is_default"] is False

    def test_create_with_set_default(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "A",
                "model_id": "gemini/x",
                "api_key": "k",
                "set_default": True,
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 201
        assert r.json()["is_default"] is True

    def test_list_omits_plaintext_keys(self, client):
        client.post(
            "/api/llm-models",
            json={"label": "A", "model_id": "g/x", "api_key": "PLAINTEXT-DO-NOT-LEAK"},
            headers=HEADERS_OK,
        )
        r = client.get("/api/llm-models", headers=HEADERS_OK)
        rows = r.json()
        assert len(rows) == 1
        assert "api_key" not in rows[0]
        body_text = r.text
        assert "PLAINTEXT-DO-NOT-LEAK" not in body_text

    def test_patch_label_only_keeps_key(self, client):
        created = client.post(
            "/api/llm-models",
            json={"label": "A", "model_id": "g/x", "api_key": "secret-1234-5678"},
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"label": "B"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["label"] == "B"
        assert body["api_key_masked"] == created["api_key_masked"]

    def test_patch_with_empty_key_keeps_existing(self, client):
        created = client.post(
            "/api/llm-models",
            json={"label": "A", "model_id": "g/x", "api_key": "secret-aaaaaa-bbbb"},
            headers=HEADERS_OK,
        ).json()
        original_masked = created["api_key_masked"]
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"label": "B", "api_key": ""},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json()["api_key_masked"] == original_masked

    def test_patch_new_key_replaces_old(self, client):
        created = client.post(
            "/api/llm-models",
            json={"label": "A", "model_id": "g/x", "api_key": "old-aaaaaaaaaa"},
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"api_key": "new-bbbbbbbbbbbb"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        new_masked = r.json()["api_key_masked"]
        assert new_masked != created["api_key_masked"]
        assert "new-" in new_masked

    def test_set_default_flips_others(self, client):
        a = client.post(
            "/api/llm-models",
            json={"label": "A", "model_id": "g/x", "api_key": "k", "set_default": True},
            headers=HEADERS_OK,
        ).json()
        b = client.post(
            "/api/llm-models",
            json={"label": "B", "model_id": "o/y", "api_key": "k"},
            headers=HEADERS_OK,
        ).json()
        r = client.post(
            f"/api/llm-models/{b['id']}/set-default",
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text

        rows = client.get("/api/llm-models", headers=HEADERS_OK).json()
        by_id = {row["id"]: row for row in rows}
        assert by_id[a["id"]]["is_default"] is False
        assert by_id[b["id"]]["is_default"] is True

    def test_set_default_missing_id_404(self, client):
        r = client.post("/api/llm-models/9999/set-default", headers=HEADERS_OK)
        assert r.status_code == 404

    def test_delete(self, client):
        created = client.post(
            "/api/llm-models",
            json={"label": "A", "model_id": "g/x", "api_key": "k"},
            headers=HEADERS_OK,
        ).json()
        r = client.delete(f"/api/llm-models/{created['id']}", headers=HEADERS_OK)
        assert r.status_code == 204
        assert client.get("/api/llm-models", headers=HEADERS_OK).json() == []

    def test_delete_missing_id_404(self, client):
        r = client.delete("/api/llm-models/9999", headers=HEADERS_OK)
        assert r.status_code == 404


class TestLogScrubbing:
    def test_scrub_api_key_kv_pattern(self):
        from api.logging import scrub_bot_tokens
        # The same global scrub should redact api_key=... patterns.
        scrubbed = scrub_bot_tokens("model=gemini/x api_key=AIzaSyMYSECRETKEYNOLEAK xyz")
        assert "AIzaSyMYSECRETKEYNOLEAK" not in scrubbed
