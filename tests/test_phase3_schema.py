"""Schema additions for Phase 3:
- LLMModel and AppSetting tables.
- ChannelPipeline.caption_ai_prompt nullable column (additive).
- app_settings seed `llm.enabled = true` on startup.
- set_default_llm_model(id) helper enforcing one-default invariant.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from api import db as _db
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    from api.db_migrations import apply_indexes, apply_schema_patches, seed_app_settings
    apply_schema_patches(eng)
    apply_indexes(eng)
    seed_app_settings(eng)
    with Session(eng) as s:
        yield s


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestLLMModelTable:
    def test_create_and_load(self, session):
        from api.models import LLMModel
        row = LLMModel(
            label="Gemini Flash Lite",
            model_id="gemini/gemini-2.0-flash-lite",
            api_key_encrypted="ciphertext",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None
        assert row.is_default is False
        assert row.created_at is not None

    def test_is_default_indexed(self, session):
        # Verify the index for is_default exists (used by "find default" lookups).
        conn = session.connection()
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='llm_models'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert any("is_default" in n for n in names), names


class TestAppSettingTable:
    def test_create_and_load(self, session):
        from api.models import AppSetting
        row = AppSetting(key="some.flag", value="true", updated_at=_now())
        session.add(row)
        session.commit()
        loaded = session.get(AppSetting, "some.flag")
        assert loaded is not None
        assert loaded.value == "true"

    def test_seed_llm_enabled_true(self, session):
        from api.models import AppSetting
        row = session.get(AppSetting, "llm.enabled")
        assert row is not None
        assert row.value == "true"

    def test_seed_is_idempotent(self, session):
        # Calling seed again must not error or duplicate.
        from api import db as _db
        from api.db_migrations import seed_app_settings
        seed_app_settings(_db.engine)
        seed_app_settings(_db.engine)
        from api.models import AppSetting
        rows = session.exec(
            select(AppSetting).where(AppSetting.key == "llm.enabled")
        ).all()
        assert len(rows) == 1


class TestCaptionAiPromptColumn:
    def test_existing_pipeline_default_null(self, session):
        from api.models import ChannelPipeline
        p = ChannelPipeline(
            name="x",
            channel_url="https://youtube.com/@x",
            fetch_count=1,
            interval_value=1,
            interval_unit="hours",
            first_fire_at=_now(),
            next_fire_at=_now(),
            state="active",
            caption_template="{title}",
            upload_options_json="{}",
            account_ids_json="[1]",
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.caption_ai_prompt is None

    def test_persist_prompt(self, session):
        from api.models import ChannelPipeline
        p = ChannelPipeline(
            name="x",
            channel_url="https://youtube.com/@x",
            fetch_count=1,
            interval_value=1,
            interval_unit="hours",
            first_fire_at=_now(),
            next_fire_at=_now(),
            state="active",
            caption_template="{ai_title} #x",
            upload_options_json="{}",
            account_ids_json="[1]",
            caption_ai_prompt="Rewrite as clickbait",
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.caption_ai_prompt == "Rewrite as clickbait"

    def test_apply_schema_patches_adds_column_to_legacy_db(self, tmp_path, monkeypatch):
        """Simulate a DB created before this branch (no caption_ai_prompt column)
        and confirm the migration adds it."""
        monkeypatch.chdir(tmp_path)
        from sqlalchemy import create_engine

        url = f"sqlite:///{tmp_path}/legacy.db"
        eng = create_engine(url)
        with eng.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE channel_pipelines ("
                "id INTEGER PRIMARY KEY, "
                "name TEXT, channel_url TEXT, fetch_count INTEGER, "
                "interval_value INTEGER, interval_unit TEXT, "
                "first_fire_at TEXT, next_fire_at TEXT, "
                "state TEXT, caption_template TEXT, "
                "upload_options_json TEXT, account_ids_json TEXT"
                ")"
            )
            conn.exec_driver_sql(
                "INSERT INTO channel_pipelines (name, channel_url, fetch_count, "
                "interval_value, interval_unit, first_fire_at, next_fire_at, "
                "state, caption_template, upload_options_json, account_ids_json) "
                "VALUES ('legacy', 'https://youtube.com/@x', 1, 1, 'hours', "
                "'2025-01-01T00:00:00', '2025-01-01T00:00:00', 'paused', "
                "'{title}', '{}', '[1]')"
            )

        from api.db_migrations import apply_schema_patches
        apply_schema_patches(eng)

        with eng.connect() as conn:
            cols = {r[1] for r in conn.exec_driver_sql(
                "PRAGMA table_info(channel_pipelines)"
            ).fetchall()}
            assert "caption_ai_prompt" in cols

            row = conn.exec_driver_sql(
                "SELECT name, caption_ai_prompt FROM channel_pipelines"
            ).fetchone()
            assert row[0] == "legacy"
            assert row[1] is None


class TestSetDefaultLLMModel:
    def test_set_default_when_none_exists(self, session):
        from api.models import LLMModel
        from api.services.llm_config import set_default_llm_model

        row = LLMModel(label="A", model_id="gemini/x", api_key_encrypted="k1")
        session.add(row)
        session.commit()
        session.refresh(row)

        set_default_llm_model(row.id)

        session.expire_all()
        loaded = session.get(LLMModel, row.id)
        assert loaded.is_default is True

    def test_set_default_flips_others_to_false(self, session):
        from api.models import LLMModel
        from api.services.llm_config import set_default_llm_model

        a = LLMModel(
            label="A", model_id="gemini/x", api_key_encrypted="k1",
            is_default=True,
        )
        b = LLMModel(label="B", model_id="openai/y", api_key_encrypted="k2")
        session.add_all([a, b])
        session.commit()
        session.refresh(a)
        session.refresh(b)

        set_default_llm_model(b.id)

        session.expire_all()
        a_after = session.get(LLMModel, a.id)
        b_after = session.get(LLMModel, b.id)
        assert a_after.is_default is False
        assert b_after.is_default is True

    def test_set_default_missing_id_raises(self, session):
        from api.services.llm_config import set_default_llm_model
        with pytest.raises(LookupError):
            set_default_llm_model(9999)
