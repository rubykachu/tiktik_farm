"""Global app settings — currently only the LLM enable flag and a
connectivity probe for Settings → Test connection."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.db import get_session
from api.schemas import (
    LLMTestRequest,
    LLMTestResponse,
    PromptTestRequest,
    PromptTestResponse,
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


@router.post("/llm/test-prompt", response_model=PromptTestResponse)
def test_prompt(payload: PromptTestRequest):
    from api.services.youtube_channel import get_video_info
    from api.services.llm_config import get_default_llm_model

    from api.schemas import is_douyin_url

    if not is_llm_enabled():
        raise HTTPException(status_code=400, detail="Tính năng LLM đang tắt trong Cài đặt")

    if is_douyin_url(payload.url):
        raise HTTPException(
            status_code=422,
            detail="Thử nghiệm prompt chỉ hỗ trợ URL YouTube. "
            "Douyin yêu cầu tải video kèm xác thực cookie nên không thể xem trước.",
        )

    try:
        info = get_video_info(payload.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Không thể tải thông tin video: {e}")

    title = info["title"]
    description = info["description"]

    try:
        if get_llm_mode() == "offline":
            model, base_url = get_ollama_config()
            if not model or not base_url:
                raise HTTPException(status_code=400, detail="OLLAMA_MODEL/OLLAMA_BASE_URL chưa được cấu hình")
            ai_title = llm.rewrite_title_offline(
                payload.prompt, title=title, description=description,
                model=model, base_url=base_url,
            )
        else:
            default = get_default_llm_model()
            if not default:
                raise HTTPException(status_code=400, detail="Chưa có model AI mặc định trong Cài đặt")
            ai_title = llm.rewrite_title(
                payload.prompt, title=title, description=description,
                model_id=default.model_id,
                api_key_encrypted=default.api_key_encrypted,
                api_base=default.api_base,
                api_version=default.api_version,
            )
    except HTTPException:
        raise
    except llm.LLMCallFailed as e:
        raise HTTPException(status_code=502, detail=f"LLM thất bại: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM thất bại: {e}")

    return PromptTestResponse(
        original_title=title,
        ai_title=ai_title or title,
        description_snippet=(description[:300] + "…") if len(description) > 300 else description,
    )
