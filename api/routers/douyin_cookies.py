"""CRUD for Douyin browser cookies stored encrypted at rest.

GET  /api/douyin-cookies          — list (id, name, created_at; no cookie_json)
POST /api/douyin-cookies          — create from Cookie-Editor JSON upload
PUT  /api/douyin-cookies/{id}     — re-upload cookie file (updates cookie_json)
DELETE /api/douyin-cookies/{id}   — delete (pipelines that reference this ID will warn in UI)
"""
from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import DouyinCookie
from api.services import secrets
from api.services.douyin_cookie_import import (
    DouyinCookieImportError,
    parse_cookie_editor_json,
)

router = APIRouter(prefix="/api/douyin-cookies", tags=["douyin-cookies"])


class DouyinCookieRead(BaseModel):
    id: int
    name: str
    created_at: object
    updated_at: object

    model_config = {"from_attributes": True}


def _parse_upload(file: UploadFile) -> str:
    raw = file.file.read()
    try:
        cookie_dict = parse_cookie_editor_json(raw)
    except DouyinCookieImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return secrets.encrypt(json.dumps(cookie_dict))


@router.get("", response_model=List[DouyinCookieRead])
def list_cookies(session: Session = Depends(get_session)):
    return session.exec(select(DouyinCookie).order_by(DouyinCookie.created_at)).all()


@router.post("", response_model=DouyinCookieRead, status_code=status.HTTP_201_CREATED)
def create_cookie(
    name: str = Form(..., min_length=1, max_length=128),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    encrypted = _parse_upload(file)
    row = DouyinCookie(name=name, cookie_json_encrypted=encrypted)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.put("/{cookie_id}", response_model=DouyinCookieRead)
def update_cookie(
    cookie_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    row = session.get(DouyinCookie, cookie_id)
    if not row:
        raise HTTPException(status_code=404, detail="douyin cookie not found")
    row.cookie_json_encrypted = _parse_upload(file)
    row.updated_at = now_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{cookie_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cookie(cookie_id: int, session: Session = Depends(get_session)):
    row = session.get(DouyinCookie, cookie_id)
    if not row:
        raise HTTPException(status_code=404, detail="douyin cookie not found")
    session.delete(row)
    session.commit()
