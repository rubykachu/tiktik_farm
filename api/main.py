"""FastAPI application entry point.

Exposes CRUD and upload endpoints backed by SQLite. OpenAPI docs at /docs
render the Pydantic schemas defined in api.schemas — this is what satisfies
"all schemas are defined and presented in the API".
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import engine as _engine, init_db
from api.db_migrations import apply_indexes, apply_schema_patches
from api.logging import install_global_scrub_filter
from api.middleware.origin import ALLOWED_ORIGINS, OriginCheckMiddleware
from api.routers import accounts, login, pipelines, schedules, telegram, uploads, videos
from tiktok_uploader.Config import Config


def create_app() -> FastAPI:
    install_global_scrub_filter()

    # Load config.txt the same way cli.py does, so paths stay consistent when
    # the api container cwd is /app.
    config_path = os.getenv("TIKTOK_CONFIG_PATH", "./config.txt")
    if os.path.exists(config_path):
        Config.load(config_path)

    app = FastAPI(
        title="TiktokAutoUploader API",
        version="1.0.0",
        description=(
            "REST API for managing TikTok accounts, uploading videos "
            "(local file or YouTube URL), and scheduling uploads. "
            "Backs the React web UI; the legacy CLI also remains supported."
        ),
    )

    # Local-first tool — webapp and api are on the same compose network and
    # the api port binds to 127.0.0.1. Tighten CORS to the localhost origins
    # the webapp actually serves from; OriginCheckMiddleware additionally
    # rejects mutating requests whose Origin/Referer is outside the whitelist
    # (defense-in-depth against CSRF from a malicious tab).
    app.add_middleware(OriginCheckMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.on_event("startup")
    def _startup() -> None:
        # Ensure CookiesDir and VideosDirPath exist on the shared volume.
        for d in (Config.get().cookies_dir, Config.get().videos_dir):
            os.makedirs(os.path.join(os.getcwd(), d), exist_ok=True)
        init_db()
        # Composite indexes SQLModel does not emit on its own.
        from api import db as _db
        apply_schema_patches(_db.engine)
        apply_indexes(_db.engine)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    app.include_router(accounts.router)
    app.include_router(videos.router)
    app.include_router(uploads.router)
    app.include_router(schedules.router)
    app.include_router(login.router)
    app.include_router(pipelines.router)
    app.include_router(telegram.router)
    return app


app = create_app()
