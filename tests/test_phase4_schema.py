"""Schema additions for Phase 4 (Douyin integration):
- douyin_cookies table
- channel_pipelines.source_type (default "youtube")
- channel_pipelines.douyin_cookie_id (nullable, no FK)
- video_uploads.douyin_aweme_id (nullable)
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


# ---- DouyinCookie table -------------------------------------------------------

class TestDouyinCookieTable:
    def test_create_and_load(self, session):
        from api.models import DouyinCookie
        row = DouyinCookie(name="Main account", cookie_json_encrypted="ciphertext123")
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None
        assert row.name == "Main account"
        assert row.cookie_json_encrypted == "ciphertext123"
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_list_multiple(self, session):
        from api.models import DouyinCookie
        session.add(DouyinCookie(name="A", cookie_json_encrypted="x1"))
        session.add(DouyinCookie(name="B", cookie_json_encrypted="x2"))
        session.commit()
        rows = session.exec(select(DouyinCookie)).all()
        assert len(rows) == 2

    def test_delete(self, session):
        from api.models import DouyinCookie
        row = DouyinCookie(name="Temp", cookie_json_encrypted="x")
        session.add(row)
        session.commit()
        session.refresh(row)
        session.delete(row)
        session.commit()
        assert session.get(DouyinCookie, row.id) is None


# ---- channel_pipelines new columns -------------------------------------------

class TestChannelPipelineSourceType:
    def _make_pipeline(self, **kwargs):
        from api.models import ChannelPipeline
        defaults = dict(
            name="test",
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
        defaults.update(kwargs)
        return ChannelPipeline(**defaults)

    def test_default_source_type_is_youtube(self, session):
        p = self._make_pipeline()
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.source_type == "youtube"

    def test_set_source_type_douyin(self, session):
        p = self._make_pipeline(source_type="douyin", douyin_cookie_id=42)
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.source_type == "douyin"
        assert p.douyin_cookie_id == 42

    def test_douyin_cookie_id_defaults_null(self, session):
        p = self._make_pipeline()
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.douyin_cookie_id is None

    def test_migration_adds_columns_to_legacy_db(self, tmp_path, monkeypatch):
        """Existing DB without source_type/douyin_cookie_id gets patched."""
        monkeypatch.chdir(tmp_path)
        from sqlalchemy import create_engine
        url = f"sqlite:///{tmp_path}/legacy4.db"
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
                "VALUES ('old', 'https://youtube.com/@x', 1, 1, 'hours', "
                "'2025-01-01T00:00:00', '2025-01-01T00:00:00', 'active', "
                "'{title}', '{}', '[1]')"
            )
        from api.db_migrations import apply_schema_patches
        apply_schema_patches(eng)
        with eng.connect() as conn:
            cols = {r[1] for r in conn.exec_driver_sql(
                "PRAGMA table_info(channel_pipelines)"
            ).fetchall()}
            assert "source_type" in cols
            assert "douyin_cookie_id" in cols
            row = conn.exec_driver_sql(
                "SELECT source_type, douyin_cookie_id FROM channel_pipelines"
            ).fetchone()
            assert row[0] == "youtube"
            assert row[1] is None


# ---- video_uploads new column ------------------------------------------------

class TestVideoUploadDouyinAwemeId:
    def test_default_aweme_id_is_null(self, session):
        from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
        acc = Account(username="u1", cookie_path="/tmp/c.cookie")
        session.add(acc)
        session.commit()
        session.refresh(acc)

        v = VideoUpload(
            account_id=acc.id,
            youtube_video_id="yt_abc",
            youtube_url="https://youtube.com/watch?v=abc",
            status="succeeded",
        )
        session.add(v)
        session.commit()
        session.refresh(v)
        assert v.douyin_aweme_id is None

    def test_set_aweme_id(self, session):
        from api.models import Account, VideoUpload
        acc = Account(username="u2", cookie_path="/tmp/c.cookie")
        session.add(acc)
        session.commit()
        session.refresh(acc)

        v = VideoUpload(
            account_id=acc.id,
            youtube_video_id="",
            youtube_url="",
            status="succeeded",
            douyin_aweme_id="7123456789012345678",
        )
        session.add(v)
        session.commit()
        session.refresh(v)
        assert v.douyin_aweme_id == "7123456789012345678"

    def test_migration_adds_column_to_legacy_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from sqlalchemy import create_engine
        url = f"sqlite:///{tmp_path}/legacy_vu.db"
        eng = create_engine(url)
        with eng.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE video_uploads ("
                "id INTEGER PRIMARY KEY, "
                "account_id INTEGER, youtube_video_id TEXT, youtube_url TEXT, status TEXT"
                ")"
            )
        from api.db_migrations import apply_schema_patches
        apply_schema_patches(eng)
        with eng.connect() as conn:
            cols = {r[1] for r in conn.exec_driver_sql(
                "PRAGMA table_info(video_uploads)"
            ).fetchall()}
            assert "douyin_aweme_id" in cols
