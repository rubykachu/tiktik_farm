"""Composite indexes that SQLModel's metadata.create_all does not gen for us.

Call `apply_indexes(engine)` at startup AFTER `init_db()`. All statements
are idempotent so repeated runs are harmless.
"""
from __future__ import annotations

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
