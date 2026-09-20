"""Seed or remove clearly-marked demo rows in the live database.

Every row it creates is named with DEMO_TAG, and `remove` deletes only rows
carrying that tag — real pipelines and history are never touched.

Demo pipelines are safe to leave running for a while: the active ones have no
TikTok account attached, so a tick could at worst fail to download from a
channel that does not exist. Paused ones never fire at all.

    python scripts/demo-data.py seed
    python scripts/demo-data.py remove
"""
from __future__ import annotations

import datetime as dt
import random
import sys

from sqlmodel import Session, select

import api.db as _db
from api.db import now_utc
from api.models import (
    Account,
    ChannelPipeline,
    PipelineRun,
    PipelineSeenVideo,
    VideoUpload,
)

DEMO_TAG = "[DEMO]"

CHANNELS = [
    "Satisfying daily", "Funny pets US", "ASMR night", "Kinetic sand",
    "Soap cutting", "Puppy clips", "Marble run", "Hydraulic press",
    "Slime mixing", "Cat fails", "Wood carving", "Pressure washing",
    "Cake decorating", "Calligraphy", "Glass blowing", "Resin art",
]
TITLES = [
    "Oddly satisfying kinetic sand cut", "Cat fails compilation #42",
    "Slime ASMR deep pressure", "Hydraulic press vs bowling ball",
    "Soap carving relaxation", "Puppy meets snow first time",
    "Marble run 4K satisfying", "Kitten steals bread",
]
ERRORS = ["cookie expired", "upload returned false", "TikTok rejected video"]


def seed() -> None:
    now = now_utc()
    random.seed(11)

    with Session(_db.engine) as s:
        if s.exec(select(ChannelPipeline).where(
            ChannelPipeline.name.startswith(DEMO_TAG)  # type: ignore[union-attr]
        )).first():
            print("Demo rows already present. Run `remove` first.")
            return

        real_account = s.exec(select(Account)).first()
        real_id = real_account.id if real_account else None

        for i, name in enumerate(CHANNELS):
            paused = i % 4 == 3
            s.add(ChannelPipeline(
                name=f"{DEMO_TAG} {name}",
                channel_url=f"https://www.youtube.com/@{name.lower().replace(' ', '')}/shorts",
                fetch_count=random.choice([1, 2, 3]),
                interval_value=random.choice([1, 2, 6]),
                interval_unit=random.choice(["hours", "days"]),
                first_fire_at=now,
                # Far enough out that the scheduler will not pick these up
                # during a demo session.
                next_fire_at=now + dt.timedelta(hours=random.randint(6, 48)),
                state="paused" if paused else "active",
                caption_template="{title} #fyp",
                upload_options_json="{}",
                # Active demo rows carry no account, so a tick cannot upload.
                account_ids_json=f"[{real_id}]" if paused and real_id else "[]",
            ))
        s.commit()

        demo = s.exec(select(ChannelPipeline).where(
            ChannelPipeline.name.startswith(DEMO_TAG)  # type: ignore[union-attr]
        )).all()

        for p in demo:
            for k in range(random.randint(2, 60)):
                started = now - dt.timedelta(hours=k * 3 + random.random())
                bad = random.random() < 0.25
                s.add(PipelineRun(
                    pipeline_id=p.id,
                    started_at=started,
                    finished_at=started + dt.timedelta(minutes=2),
                    trigger_reason="scheduled",
                    pulled_count=random.randint(0, 3),
                    uploaded_count=0 if bad else random.randint(0, 3),
                    failed_count=random.randint(1, 2) if bad else 0,
                    error_text=random.choice(ERRORS) if bad and random.random() < 0.5 else None,
                ))
        s.commit()

        if real_id:
            for i in range(120):
                ok = random.random() > 0.18
                s.add(VideoUpload(
                    pipeline_id=random.choice(demo).id,
                    account_id=real_id,
                    youtube_video_id=f"demo{i:04d}",
                    youtube_url=f"https://youtu.be/demo{i:04d}",
                    source_title=f"{DEMO_TAG} {random.choice(TITLES)}",
                    tiktok_url=f"https://tiktok.com/@demo/video/{i}" if ok else None,
                    rendered_caption=f"{random.choice(TITLES)} #fyp",
                    status="succeeded" if ok else "failed",
                    error_text=None if ok else random.choice(ERRORS),
                    attempt_count=1 if ok else random.choice([1, 2, 3]),
                    created_at=now - dt.timedelta(hours=random.random() * 96),
                ))
            s.commit()

        print(f"Seeded {len(demo)} demo pipelines, their runs, and 120 uploads.")


def remove() -> None:
    with Session(_db.engine) as s:
        demo = s.exec(select(ChannelPipeline).where(
            ChannelPipeline.name.startswith(DEMO_TAG)  # type: ignore[union-attr]
        )).all()
        ids = [p.id for p in demo]
        if not ids:
            print("No demo rows found.")
            return

        uploads = s.exec(select(VideoUpload).where(
            VideoUpload.pipeline_id.in_(ids)  # type: ignore[union-attr]
        )).all()
        runs = s.exec(select(PipelineRun).where(
            PipelineRun.pipeline_id.in_(ids)  # type: ignore[union-attr]
        )).all()
        seen = s.exec(select(PipelineSeenVideo).where(
            PipelineSeenVideo.pipeline_id.in_(ids)  # type: ignore[union-attr]
        )).all()

        # No relationship() is declared between these models, so the ORM has
        # no dependency graph to sort deletes by — it would otherwise try to
        # delete channel_pipelines before its FK children and hit SQLite's
        # FOREIGN KEY constraint. Flush children before parents explicitly.
        for row in [*uploads, *runs, *seen]:
            s.delete(row)
        s.flush()
        for row in demo:
            s.delete(row)
        s.commit()
        print(f"Removed {len(demo)} pipelines, {len(runs)} runs, {len(uploads)} uploads.")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "seed":
        seed()
    elif action == "remove":
        remove()
    else:
        print(__doc__)
        sys.exit(1)
