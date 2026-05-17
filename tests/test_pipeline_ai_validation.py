"""Phase 3 validation for caption_template + caption_ai_prompt + global LLM
config. Three required preconditions when {ai_title} appears in the template:
- caption_ai_prompt non-empty
- llm.enabled = true
- at least one LLMModel.is_default = true
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

    # Pre-create an account so pipelines accept account_ids=[1].
    from sqlmodel import Session
    from api.models import Account
    with Session(eng) as s:
        s.add(Account(username="u1", cookie_path=str(tmp_path / "u1.cookie")))
        s.commit()

    from api.main import create_app
    yield TestClient(create_app())
    _secrets._reset_for_tests()


def _pipeline_payload(**overrides):
    base = {
        "name": "test",
        "channel_url": "https://www.youtube.com/@x/videos",
        "fetch_count": 2,
        "interval_value": 6,
        "interval_unit": "hours",
        "first_fire_at": "2099-01-01T00:00:00+00:00",
        "caption_template": "{ai_title} #x",
        "caption_ai_prompt": "Rewrite as clickbait",
        "upload_options": {"allow_comment": 1, "visibility_type": 0},
        "account_ids": [1],
    }
    base.update(overrides)
    return base


def _seed_default_model(client):
    return client.post(
        "/api/llm-models",
        json={
            "label": "Gemini",
            "model_id": "gemini/gemini-2.0-flash-lite",
            "api_key": "k",
            "set_default": True,
        },
        headers=HEADERS_OK,
    )


class TestCaptionRender:
    def test_ai_title_substitutes(self):
        from api.services.caption import render_caption
        assert render_caption("{ai_title} #x", title="raw", ai_title="AI") == "AI #x"

    def test_ai_title_default_empty_when_absent(self):
        from api.services.caption import render_caption
        # Falls back to "" so old templates without {ai_title} keep working.
        assert render_caption("{title}", title="hello") == "hello"

    def test_ai_title_in_allowed_vars(self):
        from api.services.caption import ALLOWED_VARS
        assert "ai_title" in ALLOWED_VARS


class TestCreateValidation:
    def test_ai_title_without_prompt_422(self, client):
        _seed_default_model(client)
        r = client.post(
            "/api/pipelines",
            json=_pipeline_payload(caption_ai_prompt=""),
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "Add an AI prompt" in r.text

    def test_ai_title_without_default_model_422(self, client):
        # No model seeded.
        r = client.post(
            "/api/pipelines",
            json=_pipeline_payload(),
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "Configure a default LLM" in r.text

    def test_ai_title_with_llm_disabled_422(self, client):
        _seed_default_model(client)
        client.patch("/api/settings", json={"llm_enabled": False}, headers=HEADERS_OK)
        r = client.post(
            "/api/pipelines",
            json=_pipeline_payload(),
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "Enable LLM" in r.text

    def test_happy_path_with_default_model_and_prompt(self, client):
        _seed_default_model(client)
        r = client.post(
            "/api/pipelines",
            json=_pipeline_payload(),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["caption_ai_prompt"] == "Rewrite as clickbait"

    def test_existing_template_without_ai_title_still_works(self, client):
        r = client.post(
            "/api/pipelines",
            json=_pipeline_payload(
                caption_template="{title} #plain",
                caption_ai_prompt=None,
            ),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text


class TestPatchValidation:
    def test_patch_template_to_ai_title_without_prompt_422(self, client):
        _seed_default_model(client)
        # Create plain pipeline first.
        created = client.post(
            "/api/pipelines",
            json=_pipeline_payload(
                caption_template="{title}",
                caption_ai_prompt=None,
            ),
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/pipelines/{created['id']}",
            json={"caption_template": "{ai_title}"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "Add an AI prompt" in r.text

    def test_patch_adds_prompt_and_template_together(self, client):
        _seed_default_model(client)
        created = client.post(
            "/api/pipelines",
            json=_pipeline_payload(
                caption_template="{title}",
                caption_ai_prompt=None,
            ),
            headers=HEADERS_OK,
        ).json()
        r = client.patch(
            f"/api/pipelines/{created['id']}",
            json={
                "caption_template": "{ai_title} #fun",
                "caption_ai_prompt": "rewrite",
            },
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text


class TestReadExposesPrompt:
    def test_get_returns_caption_ai_prompt(self, client):
        _seed_default_model(client)
        created = client.post(
            "/api/pipelines",
            json=_pipeline_payload(),
            headers=HEADERS_OK,
        ).json()
        r = client.get(f"/api/pipelines/{created['id']}", headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json()["caption_ai_prompt"] == "Rewrite as clickbait"
