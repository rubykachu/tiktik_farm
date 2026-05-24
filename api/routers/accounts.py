"""CRUD on TikTok accounts. An account is a logical pairing of a username and
a pickled cookie file in CookiesDir. Creating an account here does NOT log in
— it registers a pickle that already exists (from CLI login or a prior noVNC
flow). Use /api/login/browser/* for the in-browser login flow.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import Account
from api.schemas import AccountCreate, AccountRead, AccountUpdate
from api.services import cookie_import, cookie_store
from api.services.safe_path import safe_join_under, validate_username

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountRead])
def list_accounts(session: Session = Depends(get_session)):
    return session.exec(select(Account).order_by(Account.username)).all()


@router.get("/{account_id}", response_model=AccountRead)
def get_account(account_id: int, session: Session = Depends(get_session)):
    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="account not found")
    return acct


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, session: Session = Depends(get_session)):
    # Require the cookie file to already exist. This endpoint is how CLI-
    # created cookies get promoted into the DB; it is NOT how new logins happen.
    if not cookie_store.exists(payload.username):
        raise HTTPException(
            status_code=400,
            detail=(
                f"No cookie file for '{payload.username}' at {cookie_store.cookie_file_path(payload.username)}. "
                "Run a login flow first (CLI or /api/login/browser/start)."
            ),
        )
    existing = session.exec(select(Account).where(Account.username == payload.username)).first()
    if existing:
        raise HTTPException(status_code=409, detail="account with that username already exists")

    acct = Account(
        username=payload.username,
        display_name=payload.display_name,
        cookie_path=cookie_store.cookie_file_path(payload.username),
        has_valid_session=cookie_store.has_valid_session(payload.username),
    )
    session.add(acct)
    session.commit()
    session.refresh(acct)
    return acct


@router.patch("/{account_id}", response_model=AccountRead)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    session: Session = Depends(get_session),
):
    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="account not found")
    if payload.display_name is not None:
        acct.display_name = payload.display_name
    acct.updated_at = now_utc()
    session.add(acct)
    session.commit()
    session.refresh(acct)
    return acct


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, session: Session = Depends(get_session)):
    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="account not found")
    # Delete DB row first — if cookie deletion fails afterwards the user can
    # still re-import. The reverse is harder to recover from.
    username = acct.username
    session.delete(acct)
    session.commit()
    cookie_store.delete(username)


@router.post("/import-json", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def import_json_cookie(
    file: UploadFile = File(...),
    username: str = Form(...),
    replace: str = Form(default="false"),
    session: Session = Depends(get_session),
):
    """Accept a Chrome Cookie-Editor JSON export, convert to pickle, store as
    `tiktok_session-<username>.cookie`, and upsert the Account row.

    Username is rejected if it contains characters outside
    `^[A-Za-z0-9_.]{1,32}$` so the resulting path can never escape CookiesDir.
    File size capped at 256KB and ≤200 cookie entries; sessionid required.
    """
    try:
        username = validate_username(username)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid username")

    raw = await file.read()
    try:
        cookies = cookie_import.parse_and_validate(raw)
    except cookie_import.CookieImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing = session.exec(select(Account).where(Account.username == username)).first()
    replace_flag = str(replace).lower() in ("true", "1", "yes", "on")
    if existing and not replace_flag:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACCOUNT_EXISTS",
                "message": (
                    f"Account '{username}' already exists. Pass replace=true to overwrite."
                ),
            },
        )

    cookies_dir = cookie_store.cookies_dir()
    try:
        cookie_path = safe_join_under(
            cookies_dir, f"{cookie_store.COOKIE_PREFIX}{username}.cookie"
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid username")

    cookie_import.save_cookies_secure(cookie_path, cookies)
    has_session = any(
        c.get("name") == "sessionid" and c.get("value") for c in cookies
    )

    if existing:
        existing.cookie_path = cookie_path
        existing.has_valid_session = has_session
        existing.updated_at = now_utc()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    acct = Account(
        username=username,
        cookie_path=cookie_path,
        has_valid_session=has_session,
    )
    session.add(acct)
    session.commit()
    session.refresh(acct)
    return acct


@router.post("/import-from-disk", response_model=List[AccountRead])
def import_from_disk(session: Session = Depends(get_session)):
    """Scan CookiesDir for cookie files not yet in the DB and register them.
    Preserves the CLI-first workflow — users who log in with `python cli.py
    login -n foo` can click one button in the UI and see their account."""
    imported: list[Account] = []
    existing = {a.username for a in session.exec(select(Account)).all()}
    for username in cookie_store.list_usernames_on_disk():
        if username in existing:
            continue
        acct = Account(
            username=username,
            cookie_path=cookie_store.cookie_file_path(username),
            has_valid_session=cookie_store.has_valid_session(username),
        )
        session.add(acct)
        imported.append(acct)
    session.commit()
    for a in imported:
        session.refresh(a)
    return imported
