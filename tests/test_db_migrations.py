"""Tests for _run_migrations — column-addition startup migration."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

import api.db as _db
from api.db import _COLUMN_MIGRATIONS, _run_migrations, make_engine


def _col_names(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


@pytest.fixture
def legacy_engine(tmp_path, monkeypatch):
    """Engine with tables created before the new columns were added.

    Simulates an existing production DB being upgraded.
    """
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from api.services import secrets as _sec
    _sec._reset_for_tests()

    engine = make_engine(f"sqlite:///{tmp_path}/legacy.db")
    _db.override_engine(engine)

    # Create tables via create_all (no migration yet — simulates old schema)
    from api import models  # noqa: F401
    SQLModel.metadata.create_all(engine)

    # Manually drop the new columns to simulate a pre-feature DB
    with engine.connect() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for column, _ in columns:
                if column in existing:
                    # SQLite doesn't support DROP COLUMN directly in older versions,
                    # so we verify they exist post-create_all (they do since models define them).
                    # For a true legacy simulation we confirm migration is idempotent when columns exist.
                    pass
        conn.commit()

    yield engine
    _db.override_engine(make_engine("sqlite:///"))
    _sec._reset_for_tests()


@pytest.fixture
def bare_engine(tmp_path, monkeypatch):
    """Engine with tables but WITHOUT the new columns — true legacy simulation.

    Creates the accounts and douyin_cookies tables with only their original
    columns by building the schema from raw SQL, bypassing SQLModel.
    """
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from api.services import secrets as _sec
    _sec._reset_for_tests()

    engine = make_engine(f"sqlite:///{tmp_path}/bare.db")
    _db.override_engine(engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                display_name TEXT,
                cookie_path TEXT NOT NULL,
                has_valid_session INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME,
                updated_at DATETIME,
                last_used_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE douyin_cookies (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                cookie_json_encrypted TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.commit()

    yield engine
    _db.override_engine(make_engine("sqlite:///"))
    _sec._reset_for_tests()


class TestMigrationOnLegacyDB:
    def test_adds_columns_to_accounts(self, bare_engine):
        before = _col_names(bare_engine, "accounts")
        assert "login" not in before
        assert "login_password_encrypted" not in before

        _run_migrations(bare_engine)

        after = _col_names(bare_engine, "accounts")
        assert "login" in after
        assert "login_password_encrypted" in after

    def test_adds_columns_to_douyin_cookies(self, bare_engine):
        before = _col_names(bare_engine, "douyin_cookies")
        assert "login" not in before
        assert "login_password_encrypted" not in before

        _run_migrations(bare_engine)

        after = _col_names(bare_engine, "douyin_cookies")
        assert "login" in after
        assert "login_password_encrypted" in after

    def test_existing_rows_have_null_for_new_columns(self, bare_engine):
        with bare_engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO accounts (username, cookie_path, has_valid_session, created_at, updated_at) "
                "VALUES ('testuser', '/tmp/cookie', 1, '2024-01-01', '2024-01-01')"
            ))
            conn.commit()

        _run_migrations(bare_engine)

        with bare_engine.connect() as conn:
            row = conn.execute(text("SELECT login, login_password_encrypted FROM accounts")).fetchone()
        assert row[0] is None
        assert row[1] is None


class TestMigrationIdempotency:
    def test_idempotent_on_already_migrated_db(self, bare_engine):
        _run_migrations(bare_engine)
        # Second call must not raise "duplicate column name"
        _run_migrations(bare_engine)

        after = _col_names(bare_engine, "accounts")
        assert "login" in after
        assert "login_password_encrypted" in after

    def test_idempotent_on_fresh_schema_from_create_all(self, tmp_path, monkeypatch):
        """create_all already includes new columns; _run_migrations must be a no-op."""
        monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
        from api.services import secrets as _sec
        _sec._reset_for_tests()

        engine = make_engine(f"sqlite:///{tmp_path}/fresh.db")
        _db.override_engine(engine)
        _db.init_db()  # create_all + _run_migrations

        # Run again — must not raise
        _run_migrations(engine)

        after = _col_names(engine, "accounts")
        assert "login" in after

        _db.override_engine(make_engine("sqlite:///"))
        _sec._reset_for_tests()


class TestMigrationConcurrency:
    def test_concurrent_migration_does_not_raise(self, bare_engine):
        """Two threads calling _run_migrations on the same SQLite file must not crash."""
        errors = []

        def run():
            try:
                _run_migrations(bare_engine)
            except Exception as e:
                errors.append(e)

        barrier = threading.Barrier(2)

        def run_with_barrier():
            barrier.wait()
            run()

        threads = [threading.Thread(target=run_with_barrier) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Either both succeed (columns added once) or one wins gracefully
        assert len(errors) == 0, f"Migration raised: {errors}"
        assert "login" in _col_names(bare_engine, "accounts")
