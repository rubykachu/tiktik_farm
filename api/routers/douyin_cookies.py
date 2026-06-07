"""CRUD for Douyin browser cookies stored encrypted at rest.

GET  /api/douyin-cookies          — list (id, name, login, login_password; no cookie_json)
POST /api/douyin-cookies          — create from Cookie-Editor JSON upload
PUT  /api/douyin-cookies/{id}     — update cookie file and/or name/login/login_password
DELETE /api/douyin-cookies/{id}   — delete (409 if a pipeline still references this ID)
"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import ChannelPipeline, DouyinCookie
from api.schemas import DouyinCookieRead
from api.services import secrets
from api.services.douyin_cookie_import import (
    DouyinCookieImportError,
    parse_cookie_editor_json,
)

router = APIRouter(prefix="/api/douyin-cookies", tags=["douyin-cookies"])


def _parse_upload(file: UploadFile) -> str:
    raw = file.file.read()
    try:
        cookie_dict = parse_cookie_editor_json(raw)
    except DouyinCookieImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return secrets.encrypt(json.dumps(cookie_dict))


def _cookie_read(row: DouyinCookie) -> DouyinCookieRead:
    return DouyinCookieRead(
        **row.model_dump(exclude={"cookie_json_encrypted", "login_password_encrypted"}),
        login_password=secrets.decrypt(row.login_password_encrypted) if row.login_password_encrypted else None,
    )


@router.get("", response_model=List[DouyinCookieRead])
def list_cookies(session: Session = Depends(get_session)):
    rows = session.exec(select(DouyinCookie).order_by(DouyinCookie.created_at)).all()
    return [_cookie_read(r) for r in rows]


@router.post("", response_model=DouyinCookieRead, status_code=status.HTTP_201_CREATED)
def create_cookie(
    name: str = Form(..., min_length=1, max_length=128),
    file: UploadFile = File(...),
    login: str = Form(default=""),
    login_password: str = Form(default=""),
    session: Session = Depends(get_session),
):
    encrypted = _parse_upload(file)
    row = DouyinCookie(
        name=name,
        cookie_json_encrypted=encrypted,
        login=login or None,
        login_password_encrypted=secrets.encrypt(login_password) if login_password else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _cookie_read(row)


@router.put("/{cookie_id}", response_model=DouyinCookieRead)
def update_cookie(
    cookie_id: int,
    file: Optional[UploadFile] = File(None),
    name: Optional[str] = Form(default=None),
    login: Optional[str] = Form(default=None),
    login_password: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
):
    row = session.get(DouyinCookie, cookie_id)
    if not row:
        raise HTTPException(status_code=404, detail="douyin cookie not found")
    if file is not None:
        row.cookie_json_encrypted = _parse_upload(file)
    if name is not None:
        row.name = name or row.name  # keep existing if sent as empty
    if login is not None:
        row.login = login or None  # empty string → NULL (clear)
    if login_password is not None:
        row.login_password_encrypted = secrets.encrypt(login_password) if login_password else None
    row.updated_at = now_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    return _cookie_read(row)


@router.delete("/{cookie_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cookie(cookie_id: int, session: Session = Depends(get_session)):
    row = session.get(DouyinCookie, cookie_id)
    if not row:
        raise HTTPException(status_code=404, detail="douyin cookie not found")
    in_use = session.exec(
        select(ChannelPipeline.name).where(
            ChannelPipeline.douyin_cookie_id == cookie_id
        )
    ).first()
    if in_use is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COOKIE_IN_USE",
                "message": f"Cookie is in use by pipeline '{in_use}'. "
                "Reassign or delete that pipeline first.",
            },
        )
    session.delete(row)
    session.commit()
