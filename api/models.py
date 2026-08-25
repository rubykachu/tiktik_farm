"""SQLModel ORM tables. Also serve as the source of truth for the DB schema
that is exposed through OpenAPI (via response_model in the routers).
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from api.db import now_utc


# ---------- accounts ---------------------------------------------------------

class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=128)
    cookie_path: str  # absolute path to tiktok_session-{username}.cookie
    has_valid_session: bool = Field(default=True)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    last_used_at: Optional[datetime] = Field(default=None)
    login: Optional[str] = Field(default=None, max_length=256)
    login_password_encrypted: Optional[str] = Field(default=None)


# ---------- channel pipelines (Phase 1) -------------------------------------

class DouyinCookie(SQLModel, table=True):
    """Douyin browser cookie stored encrypted at rest.

    cookie_json_encrypted is Fernet ciphertext (same key as LLM API keys).
    GET endpoints must never return cookie_json_encrypted or login_password_encrypted.
    """
    __tablename__ = "douyin_cookies"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    cookie_json_encrypted: str
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    login: Optional[str] = Field(default=None, max_length=256)
    login_password_encrypted: Optional[str] = Field(default=None)


class ChannelPipeline(SQLModel, table=True):
    """Recurring channel → TikTok auto-upload pipeline.

    Worker polls this table for active rows whose next_fire_at is due.
    source_type is 'youtube' or 'douyin'.
    """
    __tablename__ = "channel_pipelines"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    channel_url: str
    fetch_count: int  # top-N video per tick, 1..20 enforced at API layer.
    source_type: str = Field(default="youtube", max_length=16)  # "youtube" | "douyin"
    douyin_cookie_id: Optional[int] = Field(default=None)  # no FK — app layer handles missing

    interval_value: int  # ≥1 (≥2 when unit=minutes), enforced at API layer.
    interval_unit: str = Field(max_length=8)  # 'minutes' | 'hours' | 'days'
    first_fire_at: datetime
    next_fire_at: datetime = Field(index=True)

    state: str = Field(default="active", index=True, max_length=16)  # active|paused
    caption_template: str
    upload_options_json: str = Field(default="{}")
    account_ids_json: str = Field(default="[]")

    max_duration_seconds: Optional[int] = Field(default=None)
    upload_delay_minutes: Optional[int] = Field(default=0, ge=0, le=30)

    caption_ai_prompt: Optional[str] = Field(default=None)
    channel_description: Optional[str] = Field(default=None)
    sync_from_date: Optional[date_type] = Field(default=None)

    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)
    # Upload the video file into the Telegram chat as well as linking it.
    # Telegram caps bot uploads at 50 MB, so this is the optional extra; the
    # download link in the message text is what always works.
    telegram_attach_video: bool = Field(default=False)
    # JSON array of Account field names to include in the notification,
    # e.g. ["login", "login_password"].
    telegram_attach_fields_json: str = Field(default="[]")

    # Anti-race. lock_until is the soft TTL the worker bumps every 5min while
    # a tick is in flight. run_now_requested_at is set by POST /run-now and
    # cleared by the worker with an atomic CAS that preserves any new request
    # arriving mid-tick.
    # Schedule window: if set, pipeline re-fires every window_delay_minutes
    # from first_fire_at.time() until window_end_time (HH:MM UTC) each cycle,
    # then jumps to the next cycle's start.
    window_end_time: Optional[str] = Field(default=None, max_length=5)  # "HH:MM"
    window_delay_minutes: Optional[int] = Field(default=None, ge=3)

    run_now_requested_at: Optional[datetime] = Field(default=None)
    lock_until: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class PipelineRun(SQLModel, table=True):
    """One tick of a ChannelPipeline. Counts roll up into the inline expand
    log on the /schedules Pipelines tab and the Telegram summary message."""
    __tablename__ = "pipeline_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    pipeline_id: int = Field(foreign_key="channel_pipelines.id", index=True)
    started_at: datetime = Field(index=True)
    finished_at: Optional[datetime] = Field(default=None)
    trigger_reason: str = Field(max_length=16)  # 'scheduled' | 'manual'
    pulled_count: int = Field(default=0)
    uploaded_count: int = Field(default=0)
    skipped_dup_count: int = Field(default=0)
    skipped_exhausted_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    summary_text: str = Field(default="")
    error_text: Optional[str] = Field(default=None)
    status_text: Optional[str] = Field(default=None)
    pushed_notify_at: Optional[datetime] = Field(default=None)


class VideoUpload(SQLModel, table=True):
    """One (video, account) attempt. Inserted only AFTER the upload returns;
    no intermediate 'running' state. Powers dedupe + /videos history."""
    __tablename__ = "video_uploads"

    id: Optional[int] = Field(default=None, primary_key=True)
    # pipeline_id + run_id are hard-deleted with the parent pipeline (see
    # delete_pipeline). Nullable because legacy rows from the previous
    # SET NULL behavior may still exist.
    pipeline_id: Optional[int] = Field(
        default=None, foreign_key="channel_pipelines.id", nullable=True,
    )
    run_id: Optional[int] = Field(
        default=None, foreign_key="pipeline_runs.id", nullable=True,
    )
    account_id: int = Field(foreign_key="accounts.id", index=True)

    youtube_video_id: str = Field(max_length=64)
    youtube_url: str
    source_title: Optional[str] = Field(default=None, max_length=500)
    douyin_aweme_id: Optional[str] = Field(default=None, max_length=64)

    tiktok_video_id: Optional[str] = Field(default=None, max_length=64)
    tiktok_url: Optional[str] = Field(default=None)
    rendered_caption: Optional[str] = Field(default=None)
    ai_title: Optional[str] = Field(default=None, max_length=500)

    status: str = Field(max_length=16)  # 'succeeded' | 'failed'
    attempt_count: int = Field(default=1)
    error_text: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=now_utc, index=True)


class PipelineSeenVideo(SQLModel, table=True):
    """Dedupe key for pipelines with no TikTok account.

    The normal dedupe key is (account_id, video_id) on VideoUpload, but a
    pipeline whose account_ids_json is "[]" never writes VideoUpload rows —
    without this table it would re-download and re-notify the same videos on
    every tick. source_key is the YouTube video id or the Douyin aweme id.

    Rows are kept for the life of the pipeline — unlike PipelineRun there is no
    age cutoff, because an old video still sitting in the channel's top-N must
    stay deduped. They are deleted with their pipeline.
    """
    __tablename__ = "pipeline_seen_videos"

    id: Optional[int] = Field(default=None, primary_key=True)
    pipeline_id: int = Field(foreign_key="channel_pipelines.id", index=True)
    source_key: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=now_utc, index=True)


class ManualDownload(SQLModel, table=True):
    """A video pulled from a pasted URL for the operator to upload by hand.

    No pipeline, no account, no TikTok upload: the file is kept on disk until
    `expires_at`, then the retention purge deletes both the row and the file.
    """
    __tablename__ = "manual_downloads"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    source_type: str = Field(max_length=16)  # "youtube" | "tiktok" | "douyin"
    # "manual" (pasted URL) or "pipeline" (kept so a Telegram link can serve
    # it). No FK on pipeline_id — deleting a pipeline must not be blocked by,
    # or cascade into, a file the operator has not fetched yet.
    origin: str = Field(default="manual", max_length=16)
    pipeline_id: Optional[int] = Field(default=None)
    # queued → downloading → ready | failed
    status: str = Field(default="queued", index=True, max_length=16)
    title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None)
    file_path: Optional[str] = Field(default=None)
    file_size: Optional[int] = Field(default=None)
    duration_seconds: Optional[int] = Field(default=None)
    ai_caption: Optional[str] = Field(default=None)
    error_text: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=now_utc, index=True)
    expires_at: datetime = Field(index=True)


# ---------- LLM caption rewrite (Phase 3) -----------------------------------


class LLMModel(SQLModel, table=True):
    """User-managed LLM endpoint entry. `api_key_encrypted` is Fernet ciphertext
    produced by `api.services.secrets.encrypt`. Only one row at a time is
    `is_default=True`; the worker reads that row when a pipeline has
    `caption_ai_prompt` set."""
    __tablename__ = "llm_models"

    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = Field(max_length=128)
    model_id: str = Field(max_length=128)  # litellm "provider/model"
    api_key_encrypted: str
    api_base: Optional[str] = Field(default=None, max_length=256)
    api_version: Optional[str] = Field(default=None, max_length=32)
    is_default: bool = Field(default=False, index=True)

    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class AppSetting(SQLModel, table=True):
    """Key/value store for global flags. Currently: `llm.enabled`."""
    __tablename__ = "app_settings"

    key: str = Field(primary_key=True, max_length=64)
    value: str
    updated_at: datetime = Field(default_factory=now_utc)
