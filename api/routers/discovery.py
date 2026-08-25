"""Channel discovery: keyword chips, on-demand scans, and scan history.

Read the module docstring on api.services.channel_discovery first. There is
no automatic scheduling here — POST /scan-now starts a scan immediately as
an API background task (mirrors api.routers.downloads' manual-download
pattern) and returns right away; the webapp polls GET /scan-runs/{id} for
progress. Every DiscoveryScanRun is keyed by its keyword set, so re-running
the same set overwrites that same history entry instead of piling up.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

import api.db as _db
from api.db import get_session, now_utc
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryKeyword, DiscoveryScanResult, DiscoveryScanRun
from api.schemas import (
    DiscoveryChannelActionResponse,
    DiscoveryChannelMarkAddedRequest,
    DiscoveryChannelSkipRequest,
    DiscoveryKeywordCreate,
    DiscoveryKeywordRead,
    DiscoveryKeywordSuggestionsResponse,
    DiscoveryScanNowRequest,
    DiscoveryScanNowResponse,
    DiscoveryScanResultRead,
    DiscoveryScanRunRead,
    DiscoverySettingsRead,
    DiscoverySettingsUpdate,
)
from api.services import channel_discovery as cd
from api.services import discovery_settings as settings

log = logging.getLogger("api.routers.discovery")

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


def fail_interrupted() -> int:
    """Called on API startup. A scan's background task lives in this
    process, so a restart silently abandons anything still 'running'
    (mirrors api.routers.downloads.fail_interrupted)."""
    with Session(_db.engine) as s:
        stuck = s.exec(select(DiscoveryScanRun).where(DiscoveryScanRun.status == "running")).all()
        for row in stuck:
            row.status = "error"
            row.error_text = "interrupted by a server restart — bấm Quét ngay lại"
            row.finished_at = now_utc()
            s.add(row)
        if stuck:
            s.commit()
    return len(stuck)


# ---------- settings (scoring thresholds only) --------------------------------


def _read_settings() -> DiscoverySettingsRead:
    return DiscoverySettingsRead(
        min_subscribers=settings.get_min_subscribers(),
        silent_months=settings.get_silent_months(),
        target_days_per_video=settings.get_target_days_per_video(),
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
    if payload.view_score_reference is not None:
        settings.set_setting("discovery.view_score_reference", str(payload.view_score_reference))
    return _read_settings()


# ---------- keyword chips (persisted convenience list) ------------------------


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
def keyword_suggestions(seed: str, gl: str, hl: str):
    if not seed.strip():
        return DiscoveryKeywordSuggestionsResponse(suggestions=[])
    return DiscoveryKeywordSuggestionsResponse(suggestions=cd.suggest_keywords(seed.strip(), gl, hl))


# ---------- scan-now + history -------------------------------------------------


def _run_scan_background(scan_run_id: int, keywords: list[str], gl: str, hl: str) -> None:
    """Background worker — mirrors api.routers.downloads.run_download.
    channel_discovery.run_scan writes its own final status onto the row."""
    try:
        cd.run_scan(scan_run_id, keywords, gl, hl)
    except Exception:
        log.exception("discovery scan %d raised", scan_run_id)


@router.post("/scan-now", response_model=DiscoveryScanNowResponse)
def scan_now(
    payload: DiscoveryScanNowRequest, background: BackgroundTasks, session: Session = Depends(get_session)
):
    keywords_key = cd.normalize_keyword_set(payload.keywords)
    if not keywords_key:
        raise HTTPException(status_code=422, detail="Cần ít nhất 1 từ khóa hợp lệ")

    existing = session.exec(select(DiscoveryScanRun).where(DiscoveryScanRun.keywords_key == keywords_key)).first()
    if existing is not None and existing.status == "running":
        # Spam-click / accidental resubmit of the same set already in
        # flight — join it instead of starting a duplicate scan.
        return DiscoveryScanNowResponse(scan_run_id=existing.id, status="running")

    if existing is None:
        run = DiscoveryScanRun(
            keywords_key=keywords_key,
            keywords_json=json.dumps(payload.keywords),
            market_gl=payload.market_gl,
            market_hl=payload.market_hl,
            status="running",
        )
        session.add(run)
    else:
        run = existing
        run.keywords_json = json.dumps(payload.keywords)
        run.market_gl = payload.market_gl
        run.market_hl = payload.market_hl
        run.started_at = now_utc()
        run.finished_at = None
        run.channels_total = 0
        run.channels_done = 0
        run.candidates_found = 0
        run.status = "running"
        run.error_text = None
        session.add(run)
        session.exec(delete(DiscoveryScanResult).where(DiscoveryScanResult.scan_run_id == run.id))
    session.commit()
    session.refresh(run)

    background.add_task(_run_scan_background, run.id, payload.keywords, payload.market_gl, payload.market_hl)
    return DiscoveryScanNowResponse(scan_run_id=run.id, status="running")


@router.get("/scan-runs", response_model=list[DiscoveryScanRunRead])
def list_scan_runs(session: Session = Depends(get_session)):
    """History list — one row per unique keyword set ever scanned, most
    recent first."""
    rows = session.exec(select(DiscoveryScanRun).order_by(DiscoveryScanRun.started_at.desc())).all()
    return [_to_run_read(r) for r in rows]


@router.get("/scan-runs/{scan_run_id}", response_model=DiscoveryScanRunRead)
def get_scan_run(scan_run_id: int, session: Session = Depends(get_session)):
    run = session.get(DiscoveryScanRun, scan_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="scan run not found")
    return _to_run_read(run)


def _to_run_read(row: DiscoveryScanRun) -> DiscoveryScanRunRead:
    return DiscoveryScanRunRead(
        id=row.id,
        keywords=json.loads(row.keywords_json),
        market_gl=row.market_gl,
        market_hl=row.market_hl,
        started_at=row.started_at,
        finished_at=row.finished_at,
        channels_total=row.channels_total,
        channels_done=row.channels_done,
        candidates_found=row.candidates_found,
        status=row.status,
        error_text=row.error_text,
    )


@router.get("/scan-runs/{scan_run_id}/results", response_model=list[DiscoveryScanResultRead])
def list_scan_results(scan_run_id: int, session: Session = Depends(get_session)):
    """Results scoped to exactly this scan run — never a cross-run backlog.
    Joined against the global DiscoveryCandidate action state so an
    already-added/skipped channel shows its real status even though it was
    excluded from being re-found by a later scan."""
    if session.get(DiscoveryScanRun, scan_run_id) is None:
        raise HTTPException(status_code=404, detail="scan run not found")

    results = session.exec(
        select(DiscoveryScanResult).where(DiscoveryScanResult.scan_run_id == scan_run_id).order_by(
            DiscoveryScanResult.score.desc()
        )
    ).all()
    channel_ids = [r.channel_id for r in results]
    candidates = {
        c.channel_id: c
        for c in session.exec(
            select(DiscoveryCandidate).where(DiscoveryCandidate.channel_id.in_(channel_ids))
        ).all()
    }

    out = []
    for r in results:
        candidate = candidates.get(r.channel_id)
        out.append(
            DiscoveryScanResultRead(
                id=r.id,
                channel_id=r.channel_id,
                channel_url=r.channel_url,
                channel_title=r.channel_title,
                found_via_keyword=r.found_via_keyword,
                subscriber_count=r.subscriber_count,
                median_view_count=r.median_view_count,
                days_since_last_video=r.days_since_last_video,
                latest_video_posted_at=r.latest_video_posted_at,
                cadence_days_per_video=r.cadence_days_per_video,
                cadence_tab=r.cadence_tab,
                has_shorts_tab=r.has_shorts_tab,
                recent_shorts_count=r.recent_shorts_count,
                score=r.score,
                channel_description=r.channel_description,
                median_duration_seconds=r.median_duration_seconds,
                status=candidate.status if candidate else "new",
                added_pipeline_id=candidate.added_pipeline_id if candidate else None,
            )
        )
    return out


# ---------- per-channel actions (global state) --------------------------------


@router.post("/channels/{channel_id}/skip", response_model=DiscoveryChannelActionResponse)
def skip_channel(channel_id: str, payload: DiscoveryChannelSkipRequest, session: Session = Depends(get_session)):
    row = session.exec(select(DiscoveryCandidate).where(DiscoveryCandidate.channel_id == channel_id)).first()
    if row is None:
        row = DiscoveryCandidate(
            channel_id=channel_id, channel_url=payload.channel_url, channel_title=payload.channel_title
        )
    row.status = "skipped"
    row.updated_at = now_utc()
    session.add(row)
    session.commit()
    return DiscoveryChannelActionResponse(channel_id=channel_id, status="skipped")


@router.post("/channels/{channel_id}/mark-added", response_model=DiscoveryChannelActionResponse)
def mark_channel_added(
    channel_id: str, payload: DiscoveryChannelMarkAddedRequest, session: Session = Depends(get_session)
):
    if session.get(ChannelPipeline, payload.pipeline_id) is None:
        raise HTTPException(status_code=422, detail="pipeline not found")

    row = session.exec(select(DiscoveryCandidate).where(DiscoveryCandidate.channel_id == channel_id)).first()
    if row is None:
        row = DiscoveryCandidate(
            channel_id=channel_id, channel_url=payload.channel_url, channel_title=payload.channel_title
        )
    row.status = "added"
    row.added_pipeline_id = payload.pipeline_id
    row.updated_at = now_utc()
    session.add(row)
    session.commit()
    return DiscoveryChannelActionResponse(
        channel_id=channel_id, status="added", added_pipeline_id=payload.pipeline_id
    )
