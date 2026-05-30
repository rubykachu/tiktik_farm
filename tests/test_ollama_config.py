"""Tests for Ollama config layer (Task 1).

- get_llm_mode() defaults to "cloud" when no AppSetting row.
- set_llm_mode() persists and get_llm_mode() reads it back.
- get_ollama_config() reads env vars, falls back to defaults.
- seed_app_settings() seeds llm.mode = "cloud" idempotently.
"""
from __future__ import annotations

import pytest
from sqlmodel import SQLModel

import api.db as _api_db
import api.models  # noqa: F401 — registers all models with SQLModel.metadata


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from api.db import make_engine, override_engine
    from api.services import secrets as _secrets

    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
    _secrets._reset_for_tests()

    engine = make_engine(f"sqlite:///{tmp_path}/t.db")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)

    from api.db_migrations import apply_schema_patches, seed_app_settings
    apply_schema_patches(engine)
    seed_app_settings(engine)

    yield engine

    override_engine(make_engine("sqlite:///"))
    _secrets._reset_for_tests()


class TestGetLlmMode:
    def test_default_is_cloud_after_seed(self):
        from api.services.llm_config import get_llm_mode
        assert get_llm_mode() == "cloud"

    def test_default_is_cloud_when_no_row(self):
        from sqlmodel import Session
        from api.models import AppSetting
        from api.services.llm_config import get_llm_mode
        # Delete the seeded row to simulate missing row.
        with Session(_api_db.engine) as s:
            row = s.get(AppSetting, "llm.mode")
            if row:
                s.delete(row)
                s.commit()
        assert get_llm_mode() == "cloud"


class TestSetLlmMode:
    def test_set_offline_roundtrip(self):
        from api.services.llm_config import get_llm_mode, set_llm_mode
        set_llm_mode("offline")
        assert get_llm_mode() == "offline"

    def test_set_cloud_roundtrip(self):
        from api.services.llm_config import get_llm_mode, set_llm_mode
        set_llm_mode("offline")
        set_llm_mode("cloud")
        assert get_llm_mode() == "cloud"

    def test_idempotent_set(self):
        from api.services.llm_config import get_llm_mode, set_llm_mode
        set_llm_mode("offline")
        set_llm_mode("offline")
        assert get_llm_mode() == "offline"


class TestGetOllamaConfig:
    def test_returns_none_when_no_env(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        from api.services.llm_config import get_ollama_config
        model, base_url = get_ollama_config()
        assert model is None
        assert base_url is None

    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "mistral")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        from api.services.llm_config import get_ollama_config
        model, base_url = get_ollama_config()
        assert model == "mistral"
        assert base_url == "http://127.0.0.1:11434"

    def test_reads_host_docker_internal(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:latest")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
        from api.services.llm_config import get_ollama_config
        model, base_url = get_ollama_config()
        assert model == "llama3.2:latest"
        assert base_url == "http://host.docker.internal:11434"

    def test_invalid_base_url_raises(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://internal-server:11434")
        from api.services.llm_config import get_ollama_config
        with pytest.raises(RuntimeError, match="OLLAMA_BASE_URL"):
            get_ollama_config()


class TestSeedAppSettings:
    def test_seeds_llm_mode_cloud(self):
        from api.services.llm_config import get_llm_mode
        assert get_llm_mode() == "cloud"

    def test_seed_is_idempotent(self, fresh_db):
        from api.db_migrations import seed_app_settings
        from api.services.llm_config import get_llm_mode
        seed_app_settings(fresh_db)
        seed_app_settings(fresh_db)
        assert get_llm_mode() == "cloud"

    def test_seed_does_not_overwrite_existing_mode(self, fresh_db):
        from api.db_migrations import seed_app_settings
        from api.services.llm_config import get_llm_mode, set_llm_mode
        set_llm_mode("offline")
        seed_app_settings(fresh_db)
        assert get_llm_mode() == "offline"
