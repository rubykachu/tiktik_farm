"""CRUD for saved Telegram bot_token/chat_id configs.

GET  /api/telegram-configs        — list, ordered by created_at
POST /api/telegram-configs        — create
DELETE /api/telegram-configs/{id} — delete (no in-use guard: values are only
                                     ever copied into a pipeline, never referenced)
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from api.db import get_session
from api.models import TelegramConfig
from api.schemas import TelegramConfigCreate, TelegramConfigRead

router = APIRouter(prefix="/api/telegram-configs", tags=["telegram-configs"])


@router.get("", response_model=List[TelegramConfigRead])
def list_telegram_configs(session: Session = Depends(get_session)):
    return session.exec(
        select(TelegramConfig).order_by(TelegramConfig.created_at)
    ).all()


@router.post("", response_model=TelegramConfigRead, status_code=status.HTTP_201_CREATED)
def create_telegram_config(
    body: TelegramConfigCreate, session: Session = Depends(get_session)
):
    row = TelegramConfig(name=body.name, bot_token=body.bot_token, chat_id=body.chat_id)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_telegram_config(config_id: int, session: Session = Depends(get_session)):
    row = session.get(TelegramConfig, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="telegram config not found")
    session.delete(row)
    session.commit()
