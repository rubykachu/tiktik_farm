"""Composite indexes that SQLModel's metadata.create_all does not gen for us,
plus lightweight SQLite schema patches for additive columns.

Call `apply_schema_patches(engine)`, `apply_indexes(engine)`, and
`seed_app_settings(engine)` at startup AFTER `init_db()`. All statements are
idempotent so repeated runs are harmless.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.engine import Engine

_COMPOSITE_INDEXES = (
    # Dedupe lookup: "is there a succeeded/failed row for (account, video)?"
    "CREATE INDEX IF NOT EXISTS ix_video_upload_account_video_status "
    "ON video_uploads (account_id, youtube_video_id, status)",
    # Drill-down list for one pipeline (newest first).
    "CREATE INDEX IF NOT EXISTS ix_video_upload_pipeline_created "
    "ON video_uploads (pipeline_id, created_at DESC)",
    # /videos timeline filter by account.
    "CREATE INDEX IF NOT EXISTS ix_video_upload_account_created "
    "ON video_uploads (account_id, created_at DESC)",
)


def apply_indexes(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in _COMPOSITE_INDEXES:
            conn.exec_driver_sql(stmt)


def apply_schema_patches(engine: Engine) -> None:
    """Add columns SQLModel `create_all` will not add to existing SQLite tables."""
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(channel_pipelines)").fetchall()
        col_names = {row[1] for row in rows}
        if "upload_delay_minutes" not in col_names:
            conn.exec_driver_sql(
                "ALTER TABLE channel_pipelines ADD COLUMN "
                "upload_delay_minutes INTEGER NOT NULL DEFAULT 0"
            )
        if "caption_ai_prompt" not in col_names:
            conn.exec_driver_sql(
                "ALTER TABLE channel_pipelines ADD COLUMN caption_ai_prompt TEXT"
            )


def seed_app_settings(engine: Engine) -> None:
    """Insert default rows into app_settings on first boot. Idempotent."""
    now_iso = datetime.now(timezone.utc).isoformat()
    defaults = (
        ("llm.enabled", "true"),
    )
    with engine.begin() as conn:
        # Table may not exist yet on truly fresh installs that skip init_db()
        # in tests; bail out gracefully in that case.
        existing = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
        ).fetchone()
        if not existing:
            return
        for key, value in defaults:
            conn.exec_driver_sql(
                "INSERT OR IGNORE INTO app_settings (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (key, value, now_iso),
            )
