"""Scheduler service entrypoint.

Runs inside its own container (see scheduler/Dockerfile). Reads the same
SQLite DB as the API, polls for due ChannelPipeline rows, and delegates
execution to pipeline_worker. Serial by design (SCHEDULER_CONCURRENCY=1 in env).
"""
from __future__ import annotations

import logging
import os

from api import db as _db
from api.db import init_db
from api.db_migrations import apply_indexes, apply_schema_patches, seed_app_settings
from api.logging import install_global_scrub_filter
from scheduler import worker
from tiktok_uploader.Config import Config


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    install_global_scrub_filter()

    config_path = os.getenv("TIKTOK_CONFIG_PATH", "./config.txt")
    if os.path.exists(config_path):
        Config.load(config_path)

    init_db()
    apply_schema_patches(_db.engine)
    apply_indexes(_db.engine)
    seed_app_settings(_db.engine)
    poll = float(os.getenv("SCHEDULER_POLL_SECONDS", "30"))
    worker.loop(poll_interval=poll)


if __name__ == "__main__":
    main()
