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

    def test_patch_to_azure_without_endpoint_rejected(self, client):
        # Row starts as a non-azure provider with no endpoint fields.
        created = client.post(
            "/api/llm-models",
            json={
                "label": "OpenAI",
                "model_id": "openai/gpt-4o-mini",
                "api_key": "k",
            },
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"model_id": "azure/gpt-5.4"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text
        assert "api_base" in r.text

        # Row state preserved.
        rows = client.get("/api/llm-models", headers=HEADERS_OK).json()
        assert rows[0]["model_id"] == "openai/gpt-4o-mini"

    def test_patch_clear_api_base_on_azure_row_rejected(self, client):
        created = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_base": "https://x.openai.azure.com/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"clear_api_base": True},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text

        # Row state preserved.
        rows = client.get("/api/llm-models", headers=HEADERS_OK).json()
        assert rows[0]["api_base"] == "https://x.openai.azure.com/"
        assert rows[0]["api_version"] == "2024-08-01-preview"

    def test_patch_azure_with_only_api_base_rejected(self, client):
        created = client.post(
            "/api/llm-models",
            json={
                "label": "Azure",
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_base": "https://x.openai.azure.com/",
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        ).json()
        # Wipe api_version (set empty) via PATCH should be rejected because
        # row stays in azure/ but loses the required field.
        r = client.patch(
            f"/api/llm-models/{created['id']}",
            json={"api_version": ""},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text

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


class TestApiBaseSsrfGuard:
    """The api_base field is fed to litellm which issues an HTTP POST.
    Without validation an attacker could pivot the API host into the
    internal network (cloud metadata, Docker socket proxies, etc.)."""

    def _create(self, client, api_base: str):
        return client.post(
            "/api/llm-models",
            json={
                "label": "X",
                "model_id": "azure/gpt-5.4",
                "api_key": "k",
                "api_base": api_base,
                "api_version": "2024-08-01-preview",
            },
            headers=HEADERS_OK,
        )

    def _test_endpoint(self, client, api_base: str):
        return client.post(
            "/api/settings/llm/test",
            json={
                "model_id": "openai/gpt-4o-mini",
                "api_key": "k",
                "api_base": api_base,
            },
            headers=HEADERS_OK,
        )

    # Accept

    def test_accept_https_public_host(self, client):
        r = self._create(client, "https://x.openai.azure.com/")
        assert r.status_code == 201, r.text

    def test_accept_http_localhost(self, client):
        # Allow local Ollama / vLLM dev endpoints.
        r = self._create(client, "http://localhost:11434/v1/")
        assert r.status_code == 201, r.text

    def test_accept_http_127_0_0_1(self, client):
        r = self._create(client, "http://127.0.0.1:11434/")
        assert r.status_code == 201, r.text

    # Reject

    def test_reject_cloud_metadata_link_local(self, client):
        r = self._create(client, "http://169.254.169.254/latest/meta-data/")
        assert r.status_code == 422
        assert "private" in r.text.lower() or "loopback" in r.text.lower() or "link-local" in r.text.lower()

    def test_reject_rfc1918_10(self, client):
        r = self._create(client, "http://10.0.0.5/v1/")
        assert r.status_code == 422

    def test_reject_rfc1918_192_168(self, client):
        r = self._create(client, "http://192.168.1.5/")
        assert r.status_code == 422

    def test_reject_rfc1918_172(self, client):
        r = self._create(client, "http://172.20.0.5/")
        assert r.status_code == 422

    def test_reject_non_http_scheme(self, client):
        r = self._create(client, "ftp://example.com/")
        assert r.status_code == 422

    def test_reject_http_public_host(self, client):
        # Only https allowed for public hosts; plain http reserved for localhost.
        r = self._create(client, "http://example.com/v1/")
        assert r.status_code == 422

    def test_reject_at_test_endpoint(self, client):
        # The SSRF guard must apply on /llm/test too, otherwise an attacker
        # bypasses by hitting the probe directly without saving the row.
        r = self._test_endpoint(client, "http://169.254.169.254/")
        assert r.status_code == 422
