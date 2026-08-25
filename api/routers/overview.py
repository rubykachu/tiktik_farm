"""One request that answers "is anything wrong right now?".

The other screens each show one table and leave the operator to add the
numbers up. This endpoint exists so the first screen after login can state
the answer instead: what ran today, what broke, and what is about to run.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import (
    Account,
    ChannelPipeline,
    ManualDownload,
    PipelineRun,
    VideoUpload,
)

router = APIRouter(prefix="/api/overview", tags=["overview"])

# A run that started this long ago and never finished is the signal that the
# scheduler died mid-tick. Matches ORPHAN_STALE_AFTER in the pipeline worker.
STUCK_RUN_AFTER = timedelta(minutes=15)
# How far back "recent failures" looks. Long enough to survive a night, short
# enough that a fixed problem stops being shown.
RECENT_ERROR_WINDOW = timedelta(hours=24)
MAX_RECENT_ERRORS = 5


def _count(session: Session, statement) -> int:
    return session.exec(select(func.count()).select_from(statement.subquery())).one()


def _as_utc(value):
    """SQLite drops tzinfo; compare against an aware `now` safely."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=now_utc().tzinfo)


@router.get("")
def get_overview(session: Session = Depends(get_session)):
    now = now_utc()
    day_ago = now - timedelta(hours=24)

    uploads_24h = session.exec(
        select(VideoUpload).where(VideoUpload.created_at >= day_ago)
    ).all()
    succeeded = sum(1 for v in uploads_24h if v.status == "succeeded")
    failed = sum(1 for v in uploads_24h if v.status == "failed")

    pipelines = session.exec(select(ChannelPipeline)).all()
    active = [p for p in pipelines if p.state == "active"]

    # The earliest upcoming fire across active pipelines — the answer to
    # "is the scheduler going to do anything, and when?".
    next_fire: Optional[str] = None
    upcoming = sorted(
        (_as_utc(p.next_fire_at) for p in active if p.next_fire_at is not None)
    )
    if upcoming:
        next_fire = upcoming[0].isoformat()

    accounts = session.exec(select(Account)).all()
    invalid_accounts = [a.username for a in accounts if not a.has_valid_session]

    stuck = _count(
        session,
        select(PipelineRun).where(
            PipelineRun.finished_at.is_(None),  # type: ignore[union-attr]
            PipelineRun.started_at < now - STUCK_RUN_AFTER,
        ),
    )

    downloads_ready = _count(
        session,
        select(ManualDownload).where(
            ManualDownload.status == "ready",
            ManualDownload.expires_at >= now,
        ),
    )

    recent_errors = [
        {
            "at": v.created_at.isoformat() if v.created_at else None,
            "title": v.source_title,
            "error": v.error_text,
            "account_id": v.account_id,
        }
        for v in sorted(
            (v for v in uploads_24h if v.status == "failed" and v.error_text),
            key=lambda v: v.created_at or now,
            reverse=True,
        )[:MAX_RECENT_ERRORS]
    ]

    return {
        "uploads_24h": {"succeeded": succeeded, "failed": failed},
        "pipelines": {"active": len(active), "total": len(pipelines)},
        "accounts": {
            "total": len(accounts),
            "invalid": len(invalid_accounts),
            "invalid_usernames": invalid_accounts[:5],
        },
        "next_fire_at": next_fire,
        "stuck_runs": stuck,
        "downloads_ready": downloads_ready,
        "recent_errors": recent_errors,
        "generated_at": now.isoformat(),
    }
