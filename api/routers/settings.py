"""Global app settings — currently only the LLM enable flag and a
connectivity probe for Settings → Test connection."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.db import get_session
from api.schemas import (
    LLMTestRequest,
    LLMTestResponse,
    SettingsRead,
    SettingsUpdate,
)
from api.services import llm, secrets
from api.services.llm_config import (
    get_llm_mode,
    get_ollama_config,
    is_llm_enabled,
    set_llm_enabled,
    set_llm_mode,
)
from sqlmodel import Session  # noqa: F401  (kept for FastAPI dependency typing)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _read_settings() -> SettingsRead:
    ollama_model, ollama_base_url = get_ollama_config()
    return SettingsRead(
        llm_enabled=is_llm_enabled(),
        llm_mode=get_llm_mode(),
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url,
    )


@router.get("", response_model=SettingsRead)
def get_settings(session: Session = Depends(get_session)):
    return _read_settings()


@router.patch("", response_model=SettingsRead)
def patch_settings(payload: SettingsUpdate, session: Session = Depends(get_session)):
    if payload.llm_enabled is not None:
        set_llm_enabled(payload.llm_enabled)
    if payload.llm_mode is not None:
        set_llm_mode(payload.llm_mode)
    return _read_settings()


@router.post("/llm/test", response_model=LLMTestResponse)
def test_llm(payload: LLMTestRequest):
    ok, msg = llm.test_completion(
        model_id=payload.model_id,
        api_key_encrypted=secrets.encrypt(payload.api_key),
        api_base=payload.api_base or None,
        api_version=payload.api_version or None,
    )
    return LLMTestResponse(ok=ok, message=msg)
