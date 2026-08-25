"""Channel discovery: keyword approval, ranked candidates, and scan status.

Read the module docstring on api.services.channel_discovery first — this
router is a thin CRUD/read layer over that service and the scheduler's
discovery_worker; it never calls yt-dlp itself except for the (cheap,
synchronous) keyword-suggestion autocomplete lookup.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from api.db import get_session
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryKeyword, DiscoveryScanRun
from api.schemas import (
    DiscoveryCandidateMarkAdded,
    DiscoveryCandidateRead,
    DiscoveryKeywordCreate,
    DiscoveryKeywordRead,
    DiscoveryKeywordSuggestionsResponse,
    DiscoveryScanRunRead,
    DiscoverySettingsRead,
    DiscoverySettingsUpdate,
)
from api.services import channel_discovery as cd
from api.services import discovery_settings as settings

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


# ---------- settings -----------------------------------------------------------


def _read_settings() -> DiscoverySettingsRead:
    gl, hl = settings.get_market()
    return DiscoverySettingsRead(
        min_subscribers=settings.get_min_subscribers(),
        silent_months=settings.get_silent_months(),
        target_days_per_video=settings.get_target_days_per_video(),
        scans_per_day=settings.get_scans_per_day(),
        keywords_per_scan=settings.get_keywords_per_scan(),
        market_gl=gl,
        market_hl=hl,
        view_score_reference=settings.get_view_score_reference(),
    )


@router.get("/settings", response_model=DiscoverySettingsRead)
def get_settings():
    return _read_settings()


@router.patch("/settings", response_model=DiscoverySettingsRead)
def patch_settings(payload: DiscoverySettingsUpdate):
    if payload.min_subscribers is not None:
        settings.set_setting("discovery.min_subscribers", str(payload.min_subscribers))
    if payload.silent_months is not None:
        settings.set_setting("discovery.silent_months", str(payload.silent_months))
    if payload.target_days_per_video is not None:
        settings.set_setting("discovery.target_days_per_video", str(payload.target_days_per_video))
    if payload.scans_per_day is not None:
        settings.set_setting("discovery.scans_per_day", str(payload.scans_per_day))
    if payload.keywords_per_scan is not None:
        settings.set_setting("discovery.keywords_per_scan", str(payload.keywords_per_scan))
    if payload.market_gl is not None:
        settings.set_setting("discovery.market_gl", payload.market_gl)
    if payload.market_hl is not None:
        settings.set_setting("discovery.market_hl", payload.market_hl)
    if payload.view_score_reference is not None:
        settings.set_setting("discovery.view_score_reference", str(payload.view_score_reference))
    return _read_settings()


# ---------- keywords -----------------------------------------------------------


@router.get("/keywords", response_model=list[DiscoveryKeywordRead])
def list_keywords(session: Session = Depends(get_session)):
    rows = session.exec(select(DiscoveryKeyword).order_by(DiscoveryKeyword.created_at)).all()
    return rows


@router.post("/keywords", response_model=DiscoveryKeywordRead, status_code=status.HTTP_201_CREATED)
def create_keyword(payload: DiscoveryKeywordCreate, session: Session = Depends(get_session)):
    row = DiscoveryKeyword(keyword=payload.keyword.strip())
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Từ khóa này đã có trong danh sách")
    session.refresh(row)
    return row


@router.delete("/keywords/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_keyword(keyword_id: int, session: Session = Depends(get_session)):
    row = session.get(DiscoveryKeyword, keyword_id)
    if row is None:
        raise HTTPException(status_code=404, detail="keyword not found")
    session.delete(row)
    session.commit()


@router.get("/keyword-suggestions", response_model=DiscoveryKeywordSuggestionsResponse)
def keyword_suggestions(seed: str):
    if not seed.strip():
        return DiscoveryKeywordSuggestionsResponse(suggestions=[])
    gl, hl = settings.get_market()
    return DiscoveryKeywordSuggestionsResponse(suggestions=cd.suggest_keywords(seed.strip(), gl, hl))


# ---------- candidates ---------------------------------------------------------


@router.get("/candidates", response_model=list[DiscoveryCandidateRead])
def list_candidates(session: Session = Depends(get_session)):
    """Only 'new' candidates — skipped/added rows are done being reviewed
    and must not resurface (see api.services.channel_discovery.run_scan)."""
    rows = session.exec(
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.status == "new")
        .order_by(DiscoveryCandidate.score.desc())
    ).all()
    return rows


@router.post("/candidates/{candidate_id}/skip", response_model=DiscoveryCandidateRead)
def skip_candidate(candidate_id: int, session: Session = Depends(get_session)):
    row = session.get(DiscoveryCandidate, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    row.status = "skipped"
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.post("/candidates/{candidate_id}/mark-added", response_model=DiscoveryCandidateRead)
def mark_candidate_added(
    candidate_id: int, payload: DiscoveryCandidateMarkAdded, session: Session = Depends(get_session)
):
    row = session.get(DiscoveryCandidate, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    if session.get(ChannelPipeline, payload.pipeline_id) is None:
        raise HTTPException(status_code=422, detail="pipeline not found")
    row.status = "added"
    row.added_pipeline_id = payload.pipeline_id
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ---------- scan status ----------------------------------------------------


@router.get("/scan-status", response_model=DiscoveryScanRunRead | None)
def scan_status(session: Session = Depends(get_session)):
    """Latest scan run, for the webapp to poll while a scan is in flight."""
    row = session.exec(select(DiscoveryScanRun).order_by(DiscoveryScanRun.started_at.desc())).first()
    if row is None:
        return None
    return DiscoveryScanRunRead(
        id=row.id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        keywords=json.loads(row.keywords_json),
        channels_total=row.channels_total,
        channels_done=row.channels_done,
        candidates_found=row.candidates_found,
        status=row.status,
        error_text=row.error_text,
    )
