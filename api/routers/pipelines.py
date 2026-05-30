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
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
from api.schemas import PipelineCreate, PipelineRead, PipelineUpdate, _validate_window_fields, is_douyin_url
from api.services.caption import template_uses_ai_title
from api.services.llm_config import get_default_llm_model, get_llm_mode, is_llm_enabled
from api.services.youtube_validate import is_youtube_url


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
        "source_type": row.source_type,
        "douyin_cookie_id": row.douyin_cookie_id,
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
        "upload_delay_minutes": row.upload_delay_minutes or 0,
        "caption_ai_prompt": row.caption_ai_prompt,
        "channel_description": row.channel_description,
        "window_end_time": row.window_end_time,
        "window_delay_minutes": row.window_delay_minutes,
        "telegram_bot_token_set": bool(row.telegram_bot_token),
        "telegram_chat_id": row.telegram_chat_id,
        "run_now_requested_at": row.run_now_requested_at,
        "lock_until": row.lock_until,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _validate_ai_title_preconditions(template: str, prompt: Optional[str]) -> None:
    """Three preconditions when {ai_title} appears in the template."""
    if not template_uses_ai_title(template):
        return
    if not prompt or not prompt.strip():
        raise HTTPException(
            status_code=422,
            detail="Add an AI prompt or remove {ai_title} from the caption template",
        )
    if not is_llm_enabled():
        raise HTTPException(
            status_code=422,
            detail="Enable LLM in Settings before using {ai_title}",
        )
    # Cloud mode needs a default LLM row. Local (Ollama) mode does not —
    # it reads OLLAMA_* env vars, so skip this precondition there.
    if get_llm_mode() == "cloud" and get_default_llm_model() is None:
        raise HTTPException(
            status_code=422,
            detail="Configure a default LLM in Settings before using {ai_title}",
        )


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

    _validate_ai_title_preconditions(payload.caption_template, payload.caption_ai_prompt)

    row = ChannelPipeline(
        name=payload.name,
        channel_url=payload.channel_url,
        source_type=payload.source_type,
        douyin_cookie_id=payload.douyin_cookie_id,
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
        upload_delay_minutes=payload.upload_delay_minutes,
        caption_ai_prompt=(payload.caption_ai_prompt or None),
        channel_description=payload.channel_description or None,
        window_end_time=payload.window_end_time or None,
        window_delay_minutes=payload.window_delay_minutes,
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

    # Validate cross-field rules against the post-merge candidate values
    # BEFORE touching the row. If any validation raises, the row stays clean.
    candidate_template = (
        payload.caption_template if payload.caption_template is not None
        else row.caption_template
    )
    candidate_prompt = (
        payload.caption_ai_prompt if "caption_ai_prompt" in payload.model_fields_set
        else row.caption_ai_prompt
    )
    _validate_ai_title_preconditions(candidate_template, candidate_prompt)

    if payload.channel_url is not None:
        if row.source_type == "youtube" and not is_youtube_url(payload.channel_url):
            raise HTTPException(
                status_code=422,
                detail=[{"loc": ["body", "channel_url"], "msg": "channel_url must point to a YouTube domain", "type": "value_error"}],
            )
        if row.source_type == "douyin" and not is_douyin_url(payload.channel_url):
            raise HTTPException(
                status_code=422,
                detail=[{"loc": ["body", "channel_url"], "msg": "channel_url must point to a Douyin domain (douyin.com)", "type": "value_error"}],
            )

    changed = False
    if payload.name is not None:
        row.name = payload.name
        changed = True
    if payload.channel_url is not None:
        row.channel_url = payload.channel_url
        changed = True
    if payload.state is not None:
        if payload.state == "active" and row.state == "paused":
            # Resume — restart firing now so the user sees progress.
            row.next_fire_at = now_utc()
        elif payload.state == "paused" and row.state == "active":
            # Drop queued manual run; worker only claims active pipelines.
            row.run_now_requested_at = None
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
    if payload.first_fire_at is not None:
        row.first_fire_at = payload.first_fire_at
        if row.state == "active":
            anchor = payload.first_fire_at
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            row.next_fire_at = max(now_utc(), anchor)
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
    if payload.upload_delay_minutes is not None:
        row.upload_delay_minutes = payload.upload_delay_minutes
        changed = True
    if "caption_ai_prompt" in payload.model_fields_set:
        row.caption_ai_prompt = payload.caption_ai_prompt or None
        changed = True
    if "channel_description" in payload.model_fields_set:
        row.channel_description = payload.channel_description or None
        changed = True
    if "window_end_time" in payload.model_fields_set:
        row.window_end_time = payload.window_end_time or None
        changed = True
    if "window_delay_minutes" in payload.model_fields_set:
        row.window_delay_minutes = payload.window_delay_minutes
        changed = True

    # Post-merge window invariant checks (payload may omit either field, so we
    # validate against the merged row state, not just the payload).
    window_fields_touched = (
        "window_end_time" in payload.model_fields_set
        or "window_delay_minutes" in payload.model_fields_set
    )
    if window_fields_touched:
        # Symmetry: both set or both null.
        end_set = row.window_end_time is not None
        delay_set = row.window_delay_minutes is not None
        if end_set != delay_set:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="window_end_time and window_delay_minutes must both be set or both be null",
            )
        # interval_unit compatibility: window not allowed on minute-interval pipelines.
        effective_unit = payload.interval_unit or row.interval_unit
        if end_set and effective_unit == "minutes":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="window_end_time cannot be used when interval_unit=minutes",
            )
        # Time-ordering: use merged first_fire_at (payload may omit it).
        if end_set:
            effective_ffa = _as_utc(payload.first_fire_at or row.first_fire_at)
            try:
                _validate_window_fields(
                    row.window_end_time,
                    row.window_delay_minutes,
                    effective_unit,
                    effective_ffa,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc

    if "douyin_cookie_id" in payload.model_fields_set:
        if row.source_type != "douyin":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="douyin_cookie_id can only be set on douyin pipelines",
            )
        if payload.douyin_cookie_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="douyin_cookie_id cannot be null on a douyin pipeline",
            )
        row.douyin_cookie_id = payload.douyin_cookie_id
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

    bot_ok = bool(row.telegram_bot_token)
    chat_ok = bool((row.telegram_chat_id or "").strip())
    if bot_ok != chat_ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="telegram_bot_token and telegram_chat_id must be set together",
        )

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
    # Hard cascade: child rows are owned by the pipeline. Caller accepts
    # losing the upload history. SQLite FKs were declared NO ACTION, so we
    # purge children explicitly before deleting the parent.
    session.execute(delete(VideoUpload).where(VideoUpload.pipeline_id == pipeline_id))
    session.execute(delete(PipelineRun).where(PipelineRun.pipeline_id == pipeline_id))
    session.delete(row)
    session.commit()


@router.post("/{pipeline_id}/run-now", status_code=status.HTTP_202_ACCEPTED)
def run_now(pipeline_id: int, session: Session = Depends(get_session)):
    row = session.get(ChannelPipeline, pipeline_id)
    if not row:
        raise HTTPException(status_code=404, detail="pipeline not found")

    if row.state != "active":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PIPELINE_PAUSED",
                "message": "Pipeline is paused. Resume it before running.",
            },
        )

    lock = _as_utc(row.lock_until)
    if lock is not None and lock > now_utc():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PIPELINE_RUNNING",
                "message": "Pipeline is running. Wait for the tick to finish.",
            },
        )

    if row.run_now_requested_at is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_NOW_QUEUED",
                "message": "Run-now already queued. Wait for the next tick.",
            },
        )

    row.run_now_requested_at = now_utc()
    row.updated_at = now_utc()
    session.add(row)
    session.commit()
    return {"ok": True, "message": "Queued: pipeline will run on the next tick."}


# ─── Drill-down: runs + videos ──────────────────────────────────────────────


MAX_PAGE_SIZE = 200


@router.get("/{pipeline_id}/runs")
def list_runs(
    pipeline_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    session: Session = Depends(get_session),
):
    pipeline = session.get(ChannelPipeline, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline not found")

    offset = (page - 1) * size
    rows = session.exec(
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.started_at.desc())  # type: ignore[union-attr]
        .offset(offset)
        .limit(size)
    ).all()

    return [
        {
            "id": r.id,
            "pipeline_id": r.pipeline_id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "trigger_reason": r.trigger_reason,
            "pulled_count": r.pulled_count,
            "uploaded_count": r.uploaded_count,
            "skipped_dup_count": r.skipped_dup_count,
            "skipped_exhausted_count": r.skipped_exhausted_count,
            "failed_count": r.failed_count,
            "summary_text": r.summary_text,
            "error_text": r.error_text,
            "status_text": r.status_text,
            "pushed_notify_at": r.pushed_notify_at.isoformat() if r.pushed_notify_at else None,
        }
        for r in rows
    ]


@router.delete("/{pipeline_id}/runs", status_code=status.HTTP_204_NO_CONTENT)
def clear_runs(
    pipeline_id: int,
    run_ids: list[int] = Query(default=[]),
    session: Session = Depends(get_session),
):
    pipeline = session.get(ChannelPipeline, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline not found")
    q = (
        delete(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .where(PipelineRun.finished_at.isnot(None))
    )
    if run_ids:
        q = q.where(PipelineRun.id.in_(run_ids))
    session.execute(q)
    session.commit()


@router.get("/{pipeline_id}/runs/{run_id}/videos")
def list_run_videos(
    pipeline_id: int,
    run_id: int,
    session: Session = Depends(get_session),
):
    pipeline = session.get(ChannelPipeline, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="pipeline not found")

    run = session.get(PipelineRun, run_id)
    if not run or run.pipeline_id != pipeline_id:
        raise HTTPException(status_code=404, detail="run not found")

    rows = session.exec(
        select(VideoUpload)
        .where(VideoUpload.run_id == run_id)
        .order_by(VideoUpload.created_at.desc())  # type: ignore[union-attr]
        .limit(500)
    ).all()

    return [
        {
            "id": v.id,
            "account_id": v.account_id,
            "youtube_video_id": v.youtube_video_id,
            "youtube_url": v.youtube_url,
            "source_title": v.youtube_title,
            "douyin_aweme_id": v.douyin_aweme_id,
            "tiktok_video_id": v.tiktok_video_id,
            "tiktok_url": v.tiktok_url,
            "rendered_caption": v.rendered_caption,
            "ai_title": v.ai_title,
            "status": v.status,
            "attempt_count": v.attempt_count,
            "error_text": v.error_text,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in rows
    ]
