"""Pydantic request/response schemas — separate from SQLModel tables so we can
validate input (e.g. reject past scheduled_for, reject non-YouTube URLs) without
polluting the ORM layer. These schemas are what appears in `/docs`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from api.services.caption import find_unknown_vars
from api.services.youtube_validate import is_youtube_url as is_youtube_channel_url

_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
# Numeric only — private channels/supergroups use -100xxxxxxxxxx.
_CHAT_ID_RE = re.compile(r"^-?\d+$")


# ---------- accounts ---------------------------------------------------------

class AccountRead(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    cookie_path: str
    has_valid_session: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime]

    model_config = {"from_attributes": True}


class AccountCreate(BaseModel):
    # Register an existing cookie file (produced by CLI login or a previous
    # noVNC login) against a username. The cookie file must already exist in
    # CookiesDir; this endpoint does not *create* a login session.
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")
    display_name: Optional[str] = Field(default=None, max_length=128)


class AccountUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)


# ---------- uploads (immediate) ---------------------------------------------

class UploadOptions(BaseModel):
    """Mirrors CLI upload flags. All optional with sensible defaults."""
    allow_comment: Literal[0, 1] = 1
    allow_duet: Literal[0, 1] = 0
    allow_stitch: Literal[0, 1] = 0
    visibility_type: Literal[0, 1] = 0  # 0=public, 1=private
    brand_organic_type: Literal[0, 1] = 0
    branded_content_type: Literal[0, 1] = 0
    ai_label: Literal[0, 1] = 0
    proxy: str = ""


class UploadYouTubeRequest(BaseModel):
    username: str
    title: str = Field(min_length=1, max_length=2200)
    youtube_url: str
    options: UploadOptions = Field(default_factory=UploadOptions)

    @field_validator("youtube_url")
    @classmethod
    def _youtube(cls, v: str) -> str:
        if not is_youtube_url(v):
            raise ValueError("youtube_url must be a valid YouTube URL")
        return v


class UploadResponse(BaseModel):
    ok: bool
    message: str


# ---------- schedules --------------------------------------------------------

class ScheduledUploadCreate(BaseModel):
    username: str
    title: str = Field(min_length=1, max_length=2200)
    source_type: Literal["local", "youtube"]
    source_ref: str  # absolute path (local) or YouTube URL
    scheduled_for: datetime  # ISO-8601; must have tzinfo
    options: UploadOptions = Field(default_factory=UploadOptions)

    @model_validator(mode="after")
    def _validate(self) -> "ScheduledUploadCreate":
        # Normalize to UTC
        if self.scheduled_for.tzinfo is None:
            raise ValueError("scheduled_for must include timezone info")
        if self.scheduled_for <= datetime.now(timezone.utc):
            raise ValueError("scheduled_for must be in the future")
        if self.source_type == "youtube" and not is_youtube_url(self.source_ref):
            raise ValueError("source_ref must be a valid YouTube URL when source_type='youtube'")
        if self.options.visibility_type == 1:
            # Mirrors tiktok.py:77 — TikTok rejects scheduled private posts
            raise ValueError("private videos (visibility_type=1) cannot be scheduled")
        return self


class ScheduledUploadUpdate(BaseModel):
    scheduled_for: Optional[datetime] = None
    title: Optional[str] = Field(default=None, max_length=2200)
    status: Optional[Literal["pending", "cancelled"]] = None  # only safe transitions

    @model_validator(mode="after")
    def _validate(self) -> "ScheduledUploadUpdate":
        if self.scheduled_for is not None:
            if self.scheduled_for.tzinfo is None:
                raise ValueError("scheduled_for must include timezone info")
            if self.scheduled_for <= datetime.now(timezone.utc):
                raise ValueError("scheduled_for must be in the future")
        return self


class ScheduledUploadRead(BaseModel):
    id: int
    account_id: int
    source_type: str
    source_ref: str
    title: str
    options_json: str
    scheduled_for: datetime
    status: str
    result_text: Optional[str]
    attempts: int
    heartbeat_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- videos / browse --------------------------------------------------

class VideoFileInfo(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime


# ---------- login (noVNC) ----------------------------------------------------

class LoginBrowserStartRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")


class LoginBrowserStartResponse(BaseModel):
    session_id: str
    vnc_url: str


class LoginBrowserCompleteRequest(BaseModel):
    """Payload the noVNC control server POSTs back to the API when it detects
    the sessionid cookie. The API is responsible for writing the pickle and
    updating the account row — noVNC stays stateless."""
    cookies: list[dict]


class LoginSessionRead(BaseModel):
    id: str
    username: str
    status: str
    vnc_url: Optional[str]
    error: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ---------- shared helpers ---------------------------------------------------

# Mirrors tiktok_uploader/Video.py:75 domain list as a single regex.
_YT_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)[\w\-]+",
    re.IGNORECASE,
)


def is_youtube_url(value: str) -> bool:
    return bool(_YT_URL_RE.match(value.strip()))


# ---------- channel pipelines (Phase 1) -------------------------------------


class PipelineUploadOptions(BaseModel):
    """Subset of UploadOptions exposed in the pipeline form."""
    allow_comment: Literal[0, 1] = 1
    allow_duet: Literal[0, 1] = 0
    allow_stitch: Literal[0, 1] = 0
    visibility_type: Literal[0, 1] = 0


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    channel_url: str
    fetch_count: int = Field(ge=1, le=20)
    interval_value: int = Field(ge=1)
    interval_unit: Literal["minutes", "hours", "days"]
    first_fire_at: datetime
    caption_template: str = Field(min_length=1, max_length=2200)
    upload_options: PipelineUploadOptions = Field(default_factory=PipelineUploadOptions)
    account_ids: List[int] = Field(min_length=1)
    max_duration_seconds: Optional[int] = Field(default=None, ge=1)
    upload_delay_minutes: int = Field(default=0, ge=0, le=30)
    caption_ai_prompt: Optional[str] = Field(default=None, max_length=2000)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @field_validator("channel_url")
    @classmethod
    def _validate_channel_url(cls, v: str) -> str:
        if not is_youtube_channel_url(v):
            raise ValueError("channel_url must point to a YouTube domain")
        return v

    @field_validator("caption_template")
    @classmethod
    def _validate_caption_vars(cls, v: str) -> str:
        unknown = find_unknown_vars(v)
        if unknown:
            raise ValueError(
                f"unknown caption variable(s): {', '.join(unknown)}. "
                f"Allowed: title, title_hashtag, now"
            )
        return v

    @field_validator("telegram_bot_token")
    @classmethod
    def _validate_bot_token(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not _BOT_TOKEN_RE.match(v):
            raise ValueError("telegram_bot_token format invalid")
        return v

    @field_validator("telegram_chat_id")
    @classmethod
    def _validate_chat_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not _CHAT_ID_RE.match(v):
            raise ValueError("telegram_chat_id format invalid")
        return v

    @model_validator(mode="after")
    def _validate_interval(self):
        if self.interval_unit == "minutes" and self.interval_value < 2:
            raise ValueError("interval_value must be ≥ 2 when interval_unit=minutes")
        # Both telegram fields must be set together (or both empty).
        bot_set = bool(self.telegram_bot_token)
        chat_set = bool(self.telegram_chat_id)
        if bot_set != chat_set:
            raise ValueError(
                "telegram_bot_token and telegram_chat_id must be set together"
            )
        return self


class PipelineUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    state: Optional[Literal["active", "paused"]] = None
    fetch_count: Optional[int] = Field(default=None, ge=1, le=20)
    interval_value: Optional[int] = Field(default=None, ge=1)
    interval_unit: Optional[Literal["minutes", "hours", "days"]] = None
    first_fire_at: Optional[datetime] = None
    caption_template: Optional[str] = Field(default=None, min_length=1, max_length=2200)
    upload_options: Optional[PipelineUploadOptions] = None
    account_ids: Optional[List[int]] = None
    max_duration_seconds: Optional[int] = Field(default=None, ge=1)
    upload_delay_minutes: Optional[int] = Field(default=None, ge=0, le=30)
    caption_ai_prompt: Optional[str] = Field(default=None, max_length=2000)
    # bot_token: empty / absent → keep existing. Use clear_bot_token to remove.
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    clear_bot_token: bool = False

    @field_validator("caption_template")
    @classmethod
    def _validate_caption_vars(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        unknown = find_unknown_vars(v)
        if unknown:
            raise ValueError(
                f"unknown caption variable(s): {', '.join(unknown)}"
            )
        return v

    @field_validator("telegram_bot_token")
    @classmethod
    def _validate_bot_token(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not _BOT_TOKEN_RE.match(v):
            raise ValueError("telegram_bot_token format invalid")
        return v

    @field_validator("telegram_chat_id")
    @classmethod
    def _validate_chat_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not _CHAT_ID_RE.match(v):
            raise ValueError("telegram_chat_id format invalid")
        return v

    @model_validator(mode="after")
    def _validate_telegram_pairing(self) -> "PipelineUpdate":
        # Omitted fields mean "not changing". Final bot/chat symmetry on the stored
        # row is enforced in update_pipeline after merge.
        if self.clear_bot_token and "telegram_chat_id" in self.model_fields_set:
            cid = (self.telegram_chat_id or "").strip()
            if cid:
                raise ValueError(
                    "cannot set telegram_chat_id while clear_bot_token is true"
                )
        return self


class PipelineRead(BaseModel):
    """Bot token is NEVER exposed — only the boolean flag indicating presence."""
    id: int
    name: str
    channel_url: str
    fetch_count: int
    interval_value: int
    interval_unit: str
    first_fire_at: datetime
    next_fire_at: datetime
    state: str
    caption_template: str
    upload_options_json: str
    account_ids_json: str
    max_duration_seconds: Optional[int]
    upload_delay_minutes: int
    caption_ai_prompt: Optional[str]
    telegram_bot_token_set: bool
    telegram_chat_id: Optional[str]
    run_now_requested_at: Optional[datetime]
    lock_until: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- telegram (web UI helpers) --------------------------------------


class TelegramDetectChatsRequest(BaseModel):
    bot_token: str = Field(min_length=1)

    @field_validator("bot_token")
    @classmethod
    def _bot_token_shape(cls, v: str) -> str:
        if not _BOT_TOKEN_RE.match(v):
            raise ValueError("bot_token format invalid")
        return v


class TelegramDetectedChat(BaseModel):
    id: int
    title: str
    type: str


class TelegramDetectChatsResponse(BaseModel):
    chats: List[TelegramDetectedChat]


class TelegramTestRequest(BaseModel):
    bot_token: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)

    @field_validator("bot_token")
    @classmethod
    def _bot_token_shape(cls, v: str) -> str:
        if not _BOT_TOKEN_RE.match(v):
            raise ValueError("bot_token format invalid")
        return v

    @field_validator("chat_id")
    @classmethod
    def _chat_id_shape(cls, v: str) -> str:
        if not _CHAT_ID_RE.match(v):
            raise ValueError("chat_id format invalid")
        return v


class TelegramTestResponse(BaseModel):
    ok: bool
    message: str


# ---------- LLM caption rewrite (Phase 3) -----------------------------------


class LLMModelCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1)
    set_default: bool = False


class LLMModelUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=128)
    model_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    api_key: Optional[str] = None  # empty / absent → keep existing


class LLMModelRead(BaseModel):
    id: int
    label: str
    model_id: str
    api_key_masked: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class SettingsRead(BaseModel):
    llm_enabled: bool


class SettingsUpdate(BaseModel):
    llm_enabled: Optional[bool] = None


class LLMTestRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1)


class LLMTestResponse(BaseModel):
    ok: bool
    message: str
