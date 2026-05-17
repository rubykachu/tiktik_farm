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
from api.services.llm_config import is_llm_enabled, set_llm_enabled
from sqlmodel import Session  # noqa: F401  (kept for FastAPI dependency typing)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsRead)
def get_settings(session: Session = Depends(get_session)):
    return SettingsRead(llm_enabled=is_llm_enabled())


@router.patch("", response_model=SettingsRead)
def patch_settings(payload: SettingsUpdate, session: Session = Depends(get_session)):
    if payload.llm_enabled is not None:
        set_llm_enabled(payload.llm_enabled)
    return SettingsRead(llm_enabled=is_llm_enabled())


@router.post("/llm/test", response_model=LLMTestResponse)
def test_llm(payload: LLMTestRequest):
    ok, msg = llm.test_completion(
        model_id=payload.model_id,
        api_key_encrypted=secrets.encrypt(payload.api_key),
    )
    return LLMTestResponse(ok=ok, message=msg)
