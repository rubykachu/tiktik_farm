"""Telegram helper endpoints for the web UI (detect chat_id, test send)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import (
    TelegramDetectChatsRequest,
    TelegramDetectChatsResponse,
    TelegramTestRequest,
    TelegramTestResponse,
)
from api.services.telegram import (
    TELEGRAM_TEST_MESSAGE,
    TelegramApiError,
    detect_chats,
    send_message,
)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/detect-chats", response_model=TelegramDetectChatsResponse)
def telegram_detect_chats(body: TelegramDetectChatsRequest):
    try:
        rows = detect_chats(body.bot_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TelegramApiError:
        raise HTTPException(
            status_code=502,
            detail="Không gọi được Telegram API. Thử lại sau.",
        )
    return TelegramDetectChatsResponse(chats=rows)


@router.post("/test", response_model=TelegramTestResponse)
def telegram_test(body: TelegramTestRequest):
    try:
        ok = send_message(body.bot_token, body.chat_id, TELEGRAM_TEST_MESSAGE)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=(
                "Không gửi được tin nhắn. Kiểm tra token và quyền bot trong kênh/nhóm."
            ),
        )
    return TelegramTestResponse(
        ok=True,
        message="Tin nhắn test đã gửi thành công",
    )
