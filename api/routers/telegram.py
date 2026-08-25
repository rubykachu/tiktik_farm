"""Telegram helper endpoints for the web UI (detect chat_id, test send)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import (
    TelegramAttachFieldsResponse,
    TelegramDetectChatsRequest,
    TelegramDetectChatsResponse,
    TelegramTestRequest,
    TelegramTestResponse,
)
from api.services.account_fields import ATTACH_FIELDS
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
            detail="Cannot reach the Telegram API. Try again later.",
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
                "Could not send the message. Check the bot token and its permissions in the channel/group."
            ),
        )
    return TelegramTestResponse(
        ok=True,
        message="Test message sent successfully",
    )


@router.get("/attach-fields", response_model=TelegramAttachFieldsResponse)
def telegram_attach_fields():
    """Account fields the pipeline form can offer for Telegram notifications."""
    return TelegramAttachFieldsResponse(
        fields=[{"key": k, "label": v[0]} for k, v in ATTACH_FIELDS.items()]
    )
