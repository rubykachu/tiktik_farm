"""Tests for Azure Foundry support on /api/llm-models.

Azure requires api_base + api_version in addition to api_key. The router must
persist them, the read response must include them, and provider=azure must
reject requests missing either field.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

HEADERS_OK = {"Origin": "http://127.0.0.1:3000"}


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


class TestPersistAzureFields:
    def test_create_with_api_base_and_version(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "Azure GPT-5.4",
                "model_id": "azure/gpt-5.4",
                "api_key": "abcd1234567890",
                "api_base": "https://my-resource.openai.azure.com/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["api_base"] == "https://my-resource.openai.azure.com/"
        assert body["api_version"] == "2024-08-01-preview"

    def test_create_non_azure_leaves_fields_null(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "Gemini",
                "model_id": "gemini/gemini-2.0-flash-lite",
                "api_key": "AIzaSy1234",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["api_base"] is None
        assert body["api_version"] is None

    def test_patch_updates_api_base_and_version(self, client):
        created = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "azure/gpt-5.4",
                "api_key": "abcd",
                "api_base": "https://a.openai.azure.com/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={
                "api_base": "https://b.openai.azure.com/",
                "api_version": "2025-01-01-preview",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_base"] == "https://b.openai.azure.com/"
        assert body["api_version"] == "2025-01-01-preview"

    def test_patch_clear_api_base_wipes_both(self, client):
        created = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "openai/gpt-4o-mini",
                "api_key": "k",
                "api_base": "https://example.com/v1/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"clear_api_base": True},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_base"] is None
        assert body["api_version"] is None


class TestAzureRequiresBothFields:
    def test_azure_create_without_api_base_rejected(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "api_base" in r.text

    def test_azure_create_without_api_version_rejected(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_base": "https://x.openai.azure.com/",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "api_version" in r.text

    def test_azure_create_with_both_succeeds(self, client):
        r = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_base": "https://x.openai.azure.com/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text

    def test_azure_test_endpoint_requires_both(self, client):
        r = client.post(
            "/api/settings/llm/test",
            json={
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    @patch("api.routers.settings.llm.test_completion")
    def test_azure_test_endpoint_forwards_fields(self, mock_test, client):
        mock_test.return_value = (True, "ok")
        r = client.post(
            "/api/settings/llm/test",
            json={
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_base": "https://x.openai.azure.com/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        kw = mock_test.call_args.kwargs
        assert kw["api_base"] == "https://x.openai.azure.com/"
        assert kw["api_version"] == "2024-08-01-preview"
