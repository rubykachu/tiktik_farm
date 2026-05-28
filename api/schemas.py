"""Pydantic request/response schemas — separate from SQLModel tables so we can
validate input (e.g. reject past scheduled_for, reject non-YouTube URLs) without
polluting the ORM layer. These schemas are what appears in `/docs`.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from api.services.caption import find_unknown_vars
from api.services.youtube_validate import is_youtube_url as is_youtube_channel_url

_DOUYIN_ALLOWED_HOSTS = frozenset({
    "douyin.com",
    "www.douyin.com",
    "m.douyin.com",
    "v.douyin.com",
})


def is_douyin_url(url: str) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return u.netloc in _DOUYIN_ALLOWED_HOSTS

_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
# Numeric only — private channels/supergroups use -100xxxxxxxxxx.
_CHAT_ID_RE = re.compile(r"^-?\d+$")
_WINDOW_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ---------- douyin cookies ---------------------------------------------------

class DouyinCookieRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


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
    # Register an existing cookie file against a username. The cookie file
    # must already exist in CookiesDir; this endpoint does not create a login session.
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")
    display_name: Optional[str] = Field(default=None, max_length=128)


class AccountUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)


# ---------- videos / browse --------------------------------------------------

class VideoFileInfo(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime


# ---------- shared helpers ---------------------------------------------------

# Mirrors tiktok_uploader/Video.py:75 domain list as a single regex.
_YT_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)[\w\-]+",
    re.IGNORECASE,
)


def is_youtube_url(value: str) -> bool:
    return bool(_YT_URL_RE.match(value.strip()))


# ---------- channel pipelines (Phase 1) -------------------------------------


def _validate_window_fields(
    window_end_time: Optional[str],
    window_delay_minutes: Optional[int],
    interval_unit: Optional[str],
    first_fire_at: Optional[datetime],
) -> None:
    """Shared window validation for PipelineCreate and PipelineUpdate."""
    end_set = window_end_time is not None
    delay_set = window_delay_minutes is not None
    if end_set != delay_set:
        raise ValueError(
            "window_end_time and window_delay_minutes must both be set or both be null"
        )
    if not end_set:
        return
    if not _WINDOW_TIME_RE.match(window_end_time):  # type: ignore[arg-type]
        raise ValueError("window_end_time must be in HH:MM format (e.g. '00:30')")
    if interval_unit == "minutes":
        raise ValueError("window_end_time cannot be used when interval_unit=minutes")
    if first_fire_at is not None:
        start_h, start_m = first_fire_at.hour, first_fire_at.minute
        end_h, end_m = map(int, window_end_time.split(":"))  # type: ignore[union-attr]
        start_total = start_h * 60 + start_m
        end_total = end_h * 60 + end_m
        if end_total <= start_total:
            raise ValueError(
                "window_end_time must be later than the time component of first_fire_at"
            )
        window_minutes = end_total - start_total
        if window_minutes <= window_delay_minutes:  # type: ignore[operator]
            raise ValueError(
                "window_end_time - first_fire_at time must be > window_delay_minutes"
            )


class PipelineUploadOptions(BaseModel):
    """Subset of UploadOptions exposed in the pipeline form."""
    allow_comment: Literal[0, 1] = 1
    allow_duet: Literal[0, 1] = 0
    allow_stitch: Literal[0, 1] = 0
    visibility_type: Literal[0, 1] = 0


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    source_type: Literal["youtube", "douyin"] = "youtube"
    channel_url: str
    douyin_cookie_id: Optional[int] = None
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
    window_end_time: Optional[str] = None    # "HH:MM" UTC
    window_delay_minutes: Optional[int] = Field(default=None, ge=1)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @field_validator("channel_url")
    @classmethod
    def _validate_channel_url(cls, v: str, info: ValidationInfo) -> str:
        source_type = (info.data or {}).get("source_type", "youtube")
        if source_type == "youtube" and not is_youtube_channel_url(v):
            raise ValueError("channel_url must point to a YouTube domain")
        if source_type == "douyin" and not is_douyin_url(v):
            raise ValueError("channel_url must point to a Douyin domain (douyin.com)")
        return v

    @model_validator(mode="after")
    def _validate_source_cookie(self):
        if self.source_type == "douyin" and not self.douyin_cookie_id:
            raise ValueError("douyin_cookie_id is required when source_type=douyin")
        if self.source_type == "youtube" and self.douyin_cookie_id:
            raise ValueError("douyin_cookie_id must be null when source_type=youtube")
        return self

    @field_validator("caption_template")
    @classmethod
    def _validate_caption_vars(cls, v: str) -> str:
        from api.services.caption import ALLOWED_VARS
        unknown = find_unknown_vars(v)
        if unknown:
            raise ValueError(
                f"unknown caption variable(s): {', '.join(unknown)}. "
                f"Allowed: {', '.join(sorted(ALLOWED_VARS))}"
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
        _validate_window_fields(
            self.window_end_time,
            self.window_delay_minutes,
            self.interval_unit,
            self.first_fire_at,
        )
        return self


class PipelineUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    state: Optional[Literal["active", "paused"]] = None
    channel_url: Optional[str] = Field(default=None, min_length=1, max_length=512)
    douyin_cookie_id: Optional[int] = None
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
    window_end_time: Optional[str] = None    # "HH:MM" UTC, or null to clear
    window_delay_minutes: Optional[int] = Field(default=None, ge=1)
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
        # Validate window only when both fields are explicitly provided.
        if self.window_end_time is not None or self.window_delay_minutes is not None:
            _validate_window_fields(
                self.window_end_time,
                self.window_delay_minutes,
                self.interval_unit,
                self.first_fire_at,
            )
        return self


class PipelineRead(BaseModel):
    """Bot token is NEVER exposed — only the boolean flag indicating presence."""
    id: int
    name: str
    channel_url: str
    source_type: str = "youtube"
    douyin_cookie_id: Optional[int] = None
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
    window_end_time: Optional[str] = None
    window_delay_minutes: Optional[int] = None
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

# Format: provider/model. Constrains the provider segment to lowercase
# litellm-style identifiers so a careless typo can't open SSRF surface
# (e.g. proxy/ollama base-url style routes).
_MODEL_ID_RE = re.compile(r"^[a-z0-9_\-]+/[A-Za-z0-9._:\-]+$")

# Real provider keys are 30-200 chars; 512 is comfortable headroom and
# protects against DoS from an API-key field stuffed with megabytes.
_API_KEY_MAX = 512


def _validate_model_id(v: str) -> str:
    if not _MODEL_ID_RE.match(v):
        raise ValueError(
            "model_id must be in 'provider/model' format (e.g. gemini/gemini-2.0-flash-lite)"
        )
    return v


def _is_azure(model_id: str) -> bool:
    return model_id.lower().startswith("azure/")


# Loopback hostnames that are safe to reach from the API server even over
# plain http (typical for self-hosted Ollama / vLLM on the same box).
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _validate_api_base(v: Optional[str]) -> Optional[str]:
    """Reject schemes other than http/https and any URL whose host resolves
    to a private / loopback / link-local IP, except for explicit loopback
    hostnames over http (Ollama / vLLM dev endpoints).

    SSRF defence: an attacker who can write api_base can otherwise pivot
    /llm/test or pipeline runs into the internal network — cloud metadata
    (169.254.169.254), Docker socket proxies, internal admin UIs.
    """
    if v is None or v == "":
        return v
    parsed = urlparse(v.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("api_base scheme must be http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("api_base must include a host")
    is_loopback_name = host in _LOOPBACK_HOSTNAMES
    # IP literal — classify directly.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback:
            if parsed.scheme != "http":
                # https loopback is fine but unusual; accept it.
                pass
            return v
        if ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError(
                "api_base cannot point to a private, loopback, link-local, "
                "or reserved address"
            )
        # Public IP literal — require https.
        if parsed.scheme != "https":
            raise ValueError("api_base for a public host must use https")
        return v
    # Hostname (FQDN or "localhost") — http only for loopback names.
    if parsed.scheme == "http" and not is_loopback_name:
        raise ValueError("api_base for a public host must use https")
    return v


def require_azure_fields(model_id: str, api_base: Optional[str], api_version: Optional[str]) -> None:
    if not _is_azure(model_id):
        return
    if not (api_base and api_base.strip()):
        raise ValueError("api_base is required when provider is azure")
    if not (api_version and api_version.strip()):
        raise ValueError("api_version is required when provider is azure")


class LLMModelCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1, max_length=_API_KEY_MAX)
    api_base: Optional[str] = Field(default=None, max_length=256)
    api_version: Optional[str] = Field(default=None, max_length=32)
    set_default: bool = False

    @field_validator("model_id")
    @classmethod
    def _check_model_id(cls, v: str) -> str:
        return _validate_model_id(v)

    @field_validator("api_base")
    @classmethod
    def _check_api_base(cls, v: Optional[str]) -> Optional[str]:
        return _validate_api_base(v)

    @model_validator(mode="after")
    def _check_azure_required(self) -> "LLMModelCreate":
        require_azure_fields(self.model_id, self.api_base, self.api_version)
        return self


class LLMModelUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=128)
    model_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=_API_KEY_MAX)
    api_base: Optional[str] = Field(default=None, max_length=256)
    api_version: Optional[str] = Field(default=None, max_length=32)
    clear_api_base: bool = False

    @field_validator("model_id")
    @classmethod
    def _check_model_id(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _validate_model_id(v)

    @field_validator("api_base")
    @classmethod
    def _check_api_base(cls, v: Optional[str]) -> Optional[str]:
        return _validate_api_base(v)


class LLMModelRead(BaseModel):
    id: int
    label: str
    model_id: str
    api_key_masked: str
    api_base: Optional[str]
    api_version: Optional[str]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class SettingsRead(BaseModel):
    llm_enabled: bool
    llm_mode: Literal["cloud", "offline"]
    ollama_model: Optional[str] = None
    ollama_base_url: Optional[str] = None


class SettingsUpdate(BaseModel):
    llm_enabled: Optional[bool] = None
    llm_mode: Optional[Literal["cloud", "offline"]] = None


class LLMTestRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1, max_length=_API_KEY_MAX)
    api_base: Optional[str] = Field(default=None, max_length=256)
    api_version: Optional[str] = Field(default=None, max_length=32)

    @field_validator("model_id")
    @classmethod
    def _check_model_id(cls, v: str) -> str:
        return _validate_model_id(v)

    @field_validator("api_base")
    @classmethod
    def _check_api_base(cls, v: Optional[str]) -> Optional[str]:
        return _validate_api_base(v)

    @model_validator(mode="after")
    def _check_azure_required(self) -> "LLMTestRequest":
        require_azure_fields(self.model_id, self.api_base, self.api_version)
        return self


class LLMTestResponse(BaseModel):
    ok: bool
    message: str
