"""Upload history — backed by VideoUpload table (Phase 1).

Replaces the old disk-based file listing. Supports filters: account_id,
status, pipeline_id, date range. Paged 50/page.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import VideoUpload

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.get("")
def list_videos(
    account_id: Optional[int] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    pipeline_id: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = 1,
    size: int = 50,
    session: Session = Depends(get_session),
):
    q = select(VideoUpload)

    if account_id is not None:
        q = q.where(VideoUpload.account_id == account_id)
    if status_filter and status_filter in ("succeeded", "failed"):
        q = q.where(VideoUpload.status == status_filter)
    if pipeline_id is not None:
        q = q.where(VideoUpload.pipeline_id == pipeline_id)
    if from_date:
        try:
            dt = datetime.fromisoformat(from_date)
            q = q.where(VideoUpload.created_at >= dt)
        except ValueError:
            pass
    if to_date:
        try:
            dt = datetime.fromisoformat(to_date)
            q = q.where(VideoUpload.created_at <= dt)
        except ValueError:
            pass

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
            "youtube_title": v.youtube_title,
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
