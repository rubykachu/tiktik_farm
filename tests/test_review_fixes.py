"""Regression coverage for Phase-3 review fixes.

- Broadened log scrubber catches Bearer / x-api-key / known prefixes.
- model_id validator enforces provider/model format and rejects garbage.
- api_key length is capped.
- Partial unique index prevents two LLMModel rows with is_default=True.
- PATCH /api/pipelines rolls back row mutations on 422.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session


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

    from api.models import Account
    with Session(eng) as s:
        s.add(Account(username="u1", cookie_path=str(tmp_path / "u1.cookie")))
        s.commit()

    from fastapi.testclient import TestClient
    from api.main import create_app
    yield TestClient(create_app())
    _secrets._reset_for_tests()


HEADERS_OK = {"Origin": "http://127.0.0.1:3000"}


class TestScrubBroader:
    def test_bearer_token_redacted(self):
        from api.logging import scrub_bot_tokens
        out = scrub_bot_tokens("Authorization: Bearer sk-ant-AAAAAAAAAAAAAAAAAAAA")
        assert "sk-ant-AAAAAAAAAAAAAAAAAAAA" not in out
        assert "sk-" not in out  # known prefix scrub also fires

    def test_x_api_key_header_redacted(self):
        from api.logging import scrub_bot_tokens
        out = scrub_bot_tokens('x-api-key: AIzaSyABCDEFGHIJKLMNOPQRSTUV')
        assert "AIzaSyABCDEFGHIJKLMNOPQRSTUV" not in out

    def test_openai_prefix_in_free_text(self):
        from api.logging import scrub_bot_tokens
        out = scrub_bot_tokens("error 401 with sk-test1234567890abcdef in body")
        assert "sk-test1234567890abcdef" not in out

    def test_existing_api_key_kv_still_works(self):
        from api.logging import scrub_bot_tokens
        out = scrub_bot_tokens("model=foo api_key=secret-value-xyz tail")
        assert "secret-value-xyz" not in out


class TestModelIdValidator:
    def test_create_rejects_non_slash(self, client):
        r = client.post(
            "/api/llm-models",
            json={"label": "X", "model_id": "gemini-only", "api_key": "k"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
        assert "provider/model" in r.text

    def test_create_rejects_uppercase_provider(self, client):
        r = client.post(
            "/api/llm-models",
            json={"label": "X", "model_id": "Gemini/x", "api_key": "k"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    def test_create_accepts_valid_format(self, client):
        r = client.post(
            "/api/llm-models",
            json={"label": "X", "model_id": "gemini/gemini-2.0-flash-lite", "api_key": "k"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text

    def test_test_endpoint_validates_model_id(self, client):
        r = client.post(
            "/api/settings/llm/test",
            json={"model_id": "no-slash", "api_key": "k"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422


class TestApiKeyCap:
    def test_huge_key_rejected(self, client):
        r = client.post(
            "/api/llm-models",
            json={"label": "X", "model_id": "gemini/x", "api_key": "A" * 1000},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422


class TestPartialUniqueIndex:
    def test_two_default_rows_impossible(self, tmp_path, monkeypatch):
        """Direct DB-level invariant: cannot insert two rows with is_default=1."""
        from sqlalchemy.exc import IntegrityError

        monkeypatch.chdir(tmp_path)
        from api import db as _db
        eng = _db.make_engine(f"sqlite:///{tmp_path}/u.db")
        _db.override_engine(eng)
        _db.init_db()
        from api.db_migrations import apply_indexes, apply_schema_patches, seed_app_settings
        apply_schema_patches(eng)
        apply_indexes(eng)
        seed_app_settings(eng)

        from api.services import secrets as _secrets
        monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
        _secrets._reset_for_tests()

        from api.models import LLMModel
        with Session(eng) as s:
            s.add(LLMModel(label="A", model_id="g/x", api_key_encrypted=_secrets.encrypt("k"), is_default=True))
            s.commit()
            s.add(LLMModel(label="B", model_id="o/y", api_key_encrypted=_secrets.encrypt("k"), is_default=True))
            with pytest.raises(IntegrityError):
                s.commit()
        _secrets._reset_for_tests()


class TestPatchValidationDoesNotMutate:
    def test_failed_patch_does_not_persist_template(self, client):
        # Seed a default model so create works.
        client.post(
            "/api/llm-models",
            json={
                "label": "A",
                "model_id": "gemini/gemini-2.0-flash-lite",
                "api_key": "k",
                "set_default": True,
            },
            headers=HEADERS_OK,
        )
        # Create a pipeline with plain template + prompt.
        payload = {
            "name": "p",
            "channel_url": "https://www.youtube.com/@x/videos",
            "fetch_count": 2,
            "interval_value": 6,
            "interval_unit": "hours",
            "first_fire_at": "2099-01-01T00:00:00+00:00",
            "caption_template": "{title} plain",
            "caption_ai_prompt": None,
            "upload_options": {"allow_comment": 1, "visibility_type": 0},
            "account_ids": [1],
        }
        created = client.post("/api/pipelines", json=payload, headers=HEADERS_OK).json()

        # PATCH adds {ai_title} but no prompt. Must 422 AND must not persist
        # the new template.
        r = client.patch(
            f"/api/pipelines/{created['id']}",
            json={"caption_template": "{ai_title} fail"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

        # Fetch the pipeline again — template must be the original.
        r2 = client.get(f"/api/pipelines/{created['id']}", headers=HEADERS_OK)
        assert r2.json()["caption_template"] == "{title} plain"


class TestCaptionAllowedVarsErrorMessage:
    def test_error_lists_ai_title(self, client):
        # An unknown variable triggers the new error string.
        payload = {
            "name": "p",
            "channel_url": "https://www.youtube.com/@x/videos",
            "fetch_count": 2,
            "interval_value": 6,
            "interval_unit": "hours",
            "first_fire_at": "2099-01-01T00:00:00+00:00",
            "caption_template": "{unknown_thing}",
            "upload_options": {"allow_comment": 1, "visibility_type": 0},
            "account_ids": [1],
        }
        r = client.post("/api/pipelines", json=payload, headers=HEADERS_OK)
        assert r.status_code == 422
        body = r.text
        assert "ai_title" in body
