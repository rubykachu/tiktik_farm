"""CRUD on ChannelPipeline rows.

Bot token is never returned in responses — only the boolean
`telegram_bot_token_set`. To overwrite an existing token, PATCH a new
non-empty value. To remove it, PATCH `clear_bot_token: true`. PATCH with
empty / absent token field keeps the existing value.

DELETE refuses while `lock_until > now` so the worker can't have its row
yanked mid-tick. The user must Stop + wait.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import Account, ChannelPipeline
from api.schemas import PipelineCreate, PipelineRead, PipelineUpdate


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; coerce to UTC for safe comparison."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


def _to_read(row: ChannelPipeline) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "channel_url": row.channel_url,
        "fetch_count": row.fetch_count,
        "interval_value": row.interval_value,
        "interval_unit": row.interval_unit,
        "first_fire_at": row.first_fire_at,
        "next_fire_at": row.next_fire_at,
        "state": row.state,
        "caption_template": row.caption_template,
        "upload_options_json": row.upload_options_json,
        "account_ids_json": row.account_ids_json,
        "max_duration_seconds": row.max_duration_seconds,
        "telegram_bot_token_set": bool(row.telegram_bot_token),
        "telegram_chat_id": row.telegram_chat_id,
        "run_now_requested_at": row.run_now_requested_at,
        "lock_until": row.lock_until,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("", response_model=List[PipelineRead])
def list_pipelines(
    state: str | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(ChannelPipeline).order_by(ChannelPipeline.created_at.desc())
    if state:
        stmt = stmt.where(ChannelPipeline.state == state)
    rows = session.exec(stmt).all()
    return [_to_read(r) for r in rows]


@router.get("/{pipeline_id}", response_model=PipelineRead)
def get_pipeline(pipeline_id: int, session: Session = Depends(get_session)):
    row = session.get(ChannelPipeline, pipeline_id)
    if not row:
        raise HTTPException(status_code=404, detail="pipeline not found")
    return _to_read(row)


@router.post("", response_model=PipelineRead, status_code=status.HTTP_201_CREATED)
def create_pipeline(payload: PipelineCreate, session: Session = Depends(get_session)):
    # Confirm every requested account_id exists.
    found_ids = {
        a.id for a in session.exec(
            select(Account).where(Account.id.in_(payload.account_ids))
        ).all()
    }
    missing = sorted(set(payload.account_ids) - found_ids)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"account_id(s) do not exist: {missing}",
        )

    row = ChannelPipeline(
        name=payload.name,
        channel_url=payload.channel_url,
        fetch_count=payload.fetch_count,
        interval_value=payload.interval_value,
        interval_unit=payload.interval_unit,
        first_fire_at=payload.first_fire_at,
        next_fire_at=payload.first_fire_at,
        state="active",
        caption_template=payload.caption_template,
        upload_options_json=json.dumps(payload.upload_options.model_dump()),
        account_ids_json=json.dumps(payload.account_ids),
        max_duration_seconds=payload.max_duration_seconds,
        telegram_bot_token=payload.telegram_bot_token or None,
        telegram_chat_id=payload.telegram_chat_id or None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _to_read(row)


@router.patch("/{pipeline_id}", response_model=PipelineRead)
def update_pipeline(
    pipeline_id: int,
    payload: PipelineUpdate,
    session: Session = Depends(get_session),
):
    row = session.get(ChannelPipeline, pipeline_id)
    if not row:
        raise HTTPException(status_code=404, detail="pipeline not found")

    changed = False
    if payload.name is not None:
        row.name = payload.name
        changed = True
    if payload.state is not None:
        if payload.state == "active" and row.state == "paused":
            # Resume — restart firing now so the user sees progress.
            row.next_fire_at = now_utc()
        row.state = payload.state
        changed = True
    if payload.fetch_count is not None:
        row.fetch_count = payload.fetch_count
        changed = True
    if payload.interval_value is not None:
        row.interval_value = payload.interval_value
        changed = True
    if payload.interval_unit is not None:
        row.interval_unit = payload.interval_unit
        changed = True
    if payload.caption_template is not None:
        row.caption_template = payload.caption_template
        changed = True
    if payload.upload_options is not None:
        row.upload_options_json = json.dumps(payload.upload_options.model_dump())
        changed = True
    if payload.account_ids is not None:
        if not payload.account_ids:
            raise HTTPException(status_code=400, detail="account_ids must not be empty")
        found_ids = {
            a.id for a in session.exec(
                select(Account).where(Account.id.in_(payload.account_ids))
            ).all()
        }
        missing = sorted(set(payload.account_ids) - found_ids)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"account_id(s) do not exist: {missing}",
            )
        row.account_ids_json = json.dumps(payload.account_ids)
        changed = True
    if payload.max_duration_seconds is not None:
        row.max_duration_seconds = payload.max_duration_seconds
        changed = True

    # Telegram secret handling: only update when caller sent non-empty token,
    # or explicitly asked to clear.
    if payload.clear_bot_token:
        row.telegram_bot_token = None
        row.telegram_chat_id = None
        changed = True
    else:
        if payload.telegram_bot_token:
            row.telegram_bot_token = payload.telegram_bot_token
            changed = True
        if payload.telegram_chat_id:
            row.telegram_chat_id = payload.telegram_chat_id
            changed = True

    if changed:
        row.updated_at = now_utc()
        session.add(row)
        session.commit()
        session.refresh(row)
    return _to_read(row)


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(pipeline_id: int, session: Session = Depends(get_session)):
    row = session.get(ChannelPipeline, pipeline_id)
    if not row:
        raise HTTPException(status_code=404, detail="pipeline not found")
    lock = _as_utc(row.lock_until)
    if lock is not None and lock > now_utc():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PIPELINE_RUNNING",
                "message": "Pipeline is running. Stop it and wait for the tick to finish.",
            },
        )
    session.delete(row)
    session.commit()


@router.post("/{pipeline_id}/run-now", status_code=status.HTTP_202_ACCEPTED)
def run_now(pipeline_id: int, session: Session = Depends(get_session)):
    row = session.get(ChannelPipeline, pipeline_id)
    if not row:
        raise HTTPException(status_code=404, detail="pipeline not found")

    lock = _as_utc(row.lock_until)
    if lock is not None and lock > now_utc():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PIPELINE_RUNNING",
                "message": "Pipeline đang chạy. Đợi tick xong.",
            },
        )

    if row.run_now_requested_at is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_NOW_QUEUED",
                "message": "Đã xếp Run now, chờ tick tới.",
            },
        )

    row.run_now_requested_at = now_utc()
    row.updated_at = now_utc()
    session.add(row)
    session.commit()
    return {"ok": True, "message": "Đã xếp lịch chạy ngay"}
