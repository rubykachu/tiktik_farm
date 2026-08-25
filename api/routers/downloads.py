"""Paste a URL, get a video file back.

Fallback flow for videos the operator uploads to TikTok by hand: the download
runs in a background task, the file is served once from GET /{id}/file, and the
retention purge removes it 24h after it was created.
"""
from __future__ import annotations

import logging
import os
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlmodel import Session, select

import api.db as _db
from api.db import get_session, now_utc
from api.models import ManualDownload
from api.schemas import (
    ManualDownloadCaptionRequest,
    ManualDownloadCreate,
    ManualDownloadRead,
)
from api.services import manual_download
from api.services.caption_prompt import SAMPLE_CAPTION_PROMPT
from api.services.llm_rewrite import rewrite_with_llm

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


def run_download(download_id: int) -> None:
    """Background worker: fetch the video and record the outcome on the row."""
    with Session(_db.engine) as s:
        row = s.get(ManualDownload, download_id)
        if row is None or row.status != "queued":
            return
        row.status = "downloading"
        s.add(row)
        s.commit()
        url = row.url

    try:
        video = manual_download.download(url, manual_download.row_dir(download_id))
    except Exception as e:
        log.warning("Manual download %d failed: %s", download_id, e)
        manual_download.remove_dir(manual_download.row_dir(download_id))
        with Session(_db.engine) as s:
            row = s.get(ManualDownload, download_id)
            if row is not None:
                row.status = "failed"
                row.error_text = str(e)[:500]
                s.add(row)
                s.commit()
        return

    with Session(_db.engine) as s:
        row = s.get(ManualDownload, download_id)
        if row is None:
            # Deleted while downloading — drop the file we just fetched.
            manual_download.remove_dir(manual_download.row_dir(download_id))
            return
        row.status = "ready"
        row.title = video.title[:500] or None
        row.description = video.description or None
        row.file_path = video.file_path
        row.file_size = video.file_size
        row.duration_seconds = video.duration_seconds
        s.add(row)
        s.commit()


def fail_interrupted() -> int:
    """Mark downloads left mid-flight by a restart as failed.

    The background task lives in this process, so a restart silently abandons
    anything still queued or downloading.
    """
    with Session(_db.engine) as s:
        stuck = s.exec(
            select(ManualDownload).where(
                ManualDownload.status.in_(("queued", "downloading"))  # type: ignore[union-attr]
            )
        ).all()
        for row in stuck:
            row.status = "failed"
            row.error_text = "interrupted by a server restart — paste the URL again"
            s.add(row)
        if stuck:
            s.commit()
    return len(stuck)


@router.get("", response_model=List[ManualDownloadRead])
def list_downloads(session: Session = Depends(get_session)):
    # Retention runs here too: the scheduler's daily pass is the main purge,
    # but it only helps while that service is up. This keeps the 24h promise
    # for an API-only deployment, and no-ops when nothing has expired.
    manual_download.purge_expired()
    rows = session.exec(
        select(ManualDownload)
        .where(ManualDownload.expires_at >= now_utc())
        .order_by(ManualDownload.created_at.desc())  # type: ignore[union-attr]
    ).all()
    return rows


@router.post("", response_model=List[ManualDownloadRead], status_code=status.HTTP_201_CREATED)
def create_downloads(
    payload: ManualDownloadCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    urls = [u.strip() for u in payload.urls if u.strip()]
    if not urls:
        raise HTTPException(status_code=422, detail="no URL provided")

    rows: list[ManualDownload] = []
    for url in urls:
        try:
            source_type = manual_download.detect_source(url)
        except manual_download.UnsupportedUrl as e:
            raise HTTPException(status_code=422, detail=str(e))
        rows.append(ManualDownload(
            url=url,
            source_type=source_type,
            expires_at=now_utc() + manual_download.RETENTION,
        ))

    for row in rows:
        session.add(row)
    session.commit()
    for row in rows:
        session.refresh(row)
        background.add_task(run_download, row.id)
    return rows


# Separate prefix on purpose: nginx turns Basic Auth off for /api/public/ so a
# Telegram link works from a phone, and the HMAC in the path is what proves the
# server issued it. Nothing else may live under this prefix.
public_router = APIRouter(prefix="/api/public/downloads", tags=["downloads"])


@public_router.get("/{download_id}/{token}")
def get_signed_file(
    download_id: int, token: str, session: Session = Depends(get_session)
):
    """Serve a download to whoever holds a valid signature for it.

    The signature has no expiry — the file's 24h retention is what bounds the
    link's usefulness. Same 404 for a wrong signature and a missing row so the
    endpoint does not confirm which ids exist.
    """
    if not manual_download.verify_id(download_id, token):
        raise HTTPException(status_code=404, detail="not found")
    row = session.get(ManualDownload, download_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return _file_response(row)


def _file_response(row: ManualDownload) -> FileResponse:
    if row.status != "ready" or not row.file_path:
        raise HTTPException(status_code=409, detail=f"download is {row.status}")
    if not os.path.exists(row.file_path):
        raise HTTPException(status_code=410, detail="file no longer on disk")
    return FileResponse(
        row.file_path,
        media_type="application/octet-stream",
        filename=os.path.basename(row.file_path),
    )


@router.get("/{download_id}/file")
def get_download_file(download_id: int, session: Session = Depends(get_session)):
    row = session.get(ManualDownload, download_id)
    if not row:
        raise HTTPException(status_code=404, detail="download not found")
    return _file_response(row)


@router.post("/{download_id}/caption", response_model=ManualDownloadRead)
def generate_caption(
    download_id: int,
    payload: ManualDownloadCaptionRequest,
    session: Session = Depends(get_session),
):
    row = session.get(ManualDownload, download_id)
    if not row:
        raise HTTPException(status_code=404, detail="download not found")
    if row.status != "ready":
        raise HTTPException(status_code=409, detail=f"download is {row.status}")

    prompt = (payload.prompt or SAMPLE_CAPTION_PROMPT).strip()
    title = row.title or ""
    caption = rewrite_with_llm(prompt, title, row.description or "")
    if not caption or caption == title:
        raise HTTPException(
            status_code=422,
            detail="Không tạo được caption — kiểm tra cấu hình AI trong Cài đặt",
        )

    row.ai_caption = caption
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{download_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_download(download_id: int, session: Session = Depends(get_session)):
    row = session.get(ManualDownload, download_id)
    if not row:
        raise HTTPException(status_code=404, detail="download not found")
    manual_download.remove_dir(manual_download.row_dir(download_id))
    session.delete(row)
    session.commit()
