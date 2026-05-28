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
    # Douyin dedup lookup: "already succeeded/failed for (account, aweme)?"
    "CREATE INDEX IF NOT EXISTS ix_video_upload_account_aweme "
    "ON video_uploads (account_id, douyin_aweme_id)",
    # At-most-one default LLM model. SQLite partial index lets two writers
    # race set-default without ending up with two default rows.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_llm_models_one_default "
    "ON llm_models (is_default) WHERE is_default = 1",
)


def apply_indexes(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in _COMPOSITE_INDEXES:
            conn.exec_driver_sql(stmt)


def apply_schema_patches(engine: Engine) -> None:
    """Add columns SQLModel `create_all` will not add to existing SQLite tables."""
    with engine.begin() as conn:
        has_pipelines = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='channel_pipelines'"
        ).fetchone()
        if has_pipelines:
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
            if "source_type" not in col_names:
                conn.exec_driver_sql(
                    "ALTER TABLE channel_pipelines ADD COLUMN "
                    "source_type TEXT NOT NULL DEFAULT 'youtube'"
                )
            if "douyin_cookie_id" not in col_names:
                conn.exec_driver_sql(
                    "ALTER TABLE channel_pipelines ADD COLUMN douyin_cookie_id INTEGER"
                )
            if "window_end_time" not in col_names:
                conn.exec_driver_sql(
                    "ALTER TABLE channel_pipelines ADD COLUMN window_end_time TEXT"
                )
            if "window_delay_minutes" not in col_names:
                conn.exec_driver_sql(
                    "ALTER TABLE channel_pipelines ADD COLUMN window_delay_minutes INTEGER"
                )

        has_pipeline_runs = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_runs'"
        ).fetchone()
        if has_pipeline_runs:
            pr_rows = conn.exec_driver_sql("PRAGMA table_info(pipeline_runs)").fetchall()
            pr_cols = {row[1] for row in pr_rows}
            if "status_text" not in pr_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE pipeline_runs ADD COLUMN status_text TEXT"
                )

        # video_uploads: douyin_aweme_id for Phase 4
        has_video_uploads = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='video_uploads'"
        ).fetchone()
        if has_video_uploads:
            vu_rows = conn.exec_driver_sql("PRAGMA table_info(video_uploads)").fetchall()
            vu_cols = {row[1] for row in vu_rows}
            if "douyin_aweme_id" not in vu_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE video_uploads ADD COLUMN douyin_aweme_id TEXT"
                )

        # Azure support: api_base + api_version on llm_models. Guard against
        # a legacy DB that predates the table (init_db not called yet in some
        # test paths).
        has_llm_models = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_models'"
        ).fetchone()
        if has_llm_models:
            llm_rows = conn.exec_driver_sql("PRAGMA table_info(llm_models)").fetchall()
            llm_cols = {row[1] for row in llm_rows}
            if "api_base" not in llm_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE llm_models ADD COLUMN api_base TEXT"
                )
            if "api_version" not in llm_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE llm_models ADD COLUMN api_version TEXT"
                )


def seed_app_settings(engine: Engine) -> None:
    """Insert default rows into app_settings on first boot. Idempotent."""
    now_iso = datetime.now(timezone.utc).isoformat()
    defaults = (
        ("llm.enabled", "true"),
        ("llm.mode", "cloud"),
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
