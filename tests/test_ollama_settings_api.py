"""Tests for Ollama mode in GET/PATCH /api/settings (Task 3).

- GET returns llm_mode, ollama_model, ollama_base_url.
- PATCH {llm_mode} persists and roundtrips correctly.
- PATCH with invalid llm_mode → 422.
- Existing llm_enabled field unaffected.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HEADERS_OK = {"Origin": "http://127.0.0.1:3000"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
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


class TestGetSettingsOllama:
    def test_returns_llm_mode_cloud_by_default(self, client):
        r = client.get("/api/settings", headers=HEADERS_OK)
        assert r.status_code == 200
        body = r.json()
        assert body["llm_mode"] == "cloud"

    def test_returns_ollama_model_and_base_url(self, client):
        r = client.get("/api/settings", headers=HEADERS_OK)
        body = r.json()
        assert "ollama_model" in body
        assert "ollama_base_url" in body

    def test_ollama_defaults_when_env_not_set(self, client):
        r = client.get("/api/settings", headers=HEADERS_OK)
        body = r.json()
        assert body["ollama_model"] == "llama3.2"
        assert body["ollama_base_url"] == "http://localhost:11434"

    def test_ollama_reads_env_vars(self, client, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "mistral")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-server:11434")
        r = client.get("/api/settings", headers=HEADERS_OK)
        body = r.json()
        assert body["ollama_model"] == "mistral"
        assert body["ollama_base_url"] == "http://gpu-server:11434"

    def test_existing_llm_enabled_still_present(self, client):
        r = client.get("/api/settings", headers=HEADERS_OK)
        body = r.json()
        assert "llm_enabled" in body
        assert body["llm_enabled"] is True


class TestPatchSettingsOllama:
    def test_set_mode_offline(self, client):
        r = client.patch(
            "/api/settings",
            json={"llm_mode": "offline"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json()["llm_mode"] == "offline"

    def test_toggle_offline_then_cloud(self, client):
        client.patch("/api/settings", json={"llm_mode": "offline"}, headers=HEADERS_OK)
        r = client.patch("/api/settings", json={"llm_mode": "cloud"}, headers=HEADERS_OK)
        assert r.json()["llm_mode"] == "cloud"

    def test_mode_persists_across_requests(self, client):
        client.patch("/api/settings", json={"llm_mode": "offline"}, headers=HEADERS_OK)
        r = client.get("/api/settings", headers=HEADERS_OK)
        assert r.json()["llm_mode"] == "offline"

    def test_invalid_mode_returns_422(self, client):
        r = client.patch(
            "/api/settings",
            json={"llm_mode": "bad-value"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    def test_partial_patch_does_not_reset_mode(self, client):
        client.patch("/api/settings", json={"llm_mode": "offline"}, headers=HEADERS_OK)
        client.patch("/api/settings", json={"llm_enabled": False}, headers=HEADERS_OK)
        r = client.get("/api/settings", headers=HEADERS_OK)
        assert r.json()["llm_mode"] == "offline"

    def test_patch_mode_does_not_change_llm_enabled(self, client):
        client.patch("/api/settings", json={"llm_enabled": False}, headers=HEADERS_OK)
        client.patch("/api/settings", json={"llm_mode": "offline"}, headers=HEADERS_OK)
        r = client.get("/api/settings", headers=HEADERS_OK)
        assert r.json()["llm_enabled"] is False
