"""Upload history — backed by VideoUpload table (Phase 1)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from api.db import get_session
from api.models import VideoUpload

router = APIRouter(prefix="/api/videos", tags=["videos"])

MAX_PAGE_SIZE = 200

_VALID_STATUSES = {"succeeded", "failed"}


def _parse_date(value: Optional[str], param_name: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date format for '{param_name}': {value!r}. Use ISO 8601.",
        )


@router.get("")
def list_videos(
    account_id: Optional[int] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    pipeline_id: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    session: Session = Depends(get_session),
):
    q = select(VideoUpload)

    if account_id is not None:
        q = q.where(VideoUpload.account_id == account_id)
    if status_filter:
        if status_filter not in _VALID_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status filter: {status_filter!r}. Must be one of: {sorted(_VALID_STATUSES)}",
            )
        q = q.where(VideoUpload.status == status_filter)
    if pipeline_id is not None:
        q = q.where(VideoUpload.pipeline_id == pipeline_id)

    dt_from = _parse_date(from_date, "from_date")
    if dt_from:
        q = q.where(VideoUpload.created_at >= dt_from)
    dt_to = _parse_date(to_date, "to_date")
    if dt_to:
        q = q.where(VideoUpload.created_at <= dt_to)

    offset = (page - 1) * size
    rows = session.exec(
        q.order_by(VideoUpload.created_at.desc())  # type: ignore[union-attr]
        .offset(offset)
        .limit(size)
    ).all()

    return [
        {
            "id": v.id,
            "pipeline_id": v.pipeline_id,
            "run_id": v.run_id,
            "account_id": v.account_id,
            "youtube_video_id": v.youtube_video_id,
            "youtube_url": v.youtube_url,
            "source_title": v.source_title,
            "tiktok_video_id": v.tiktok_video_id,
            "tiktok_url": v.tiktok_url,
            "rendered_caption": v.rendered_caption,
            "status": v.status,
            "attempt_count": v.attempt_count,
            "error_text": v.error_text,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in rows
    ]


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(video_id: int, session: Session = Depends(get_session)):
    row = session.get(VideoUpload, video_id)
    if not row:
        raise HTTPException(status_code=404, detail="video upload not found")
    session.delete(row)
    session.commit()
