"""Scheduler entry points.

tick() and loop() are called by scheduler/main.py.
All upload execution is delegated to pipeline_worker.

Channel discovery scans are NOT scheduled — they run as an API background
task triggered by the "Quét ngay" button (api.routers.discovery.scan_now),
not by this loop.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor

from api.services.manual_download import purge_expired as purge_expired_downloads
from scheduler.pipeline_worker import pipeline_tick, purge_old_runs, reap_orphans

log = logging.getLogger("scheduler.worker")

_PURGE_INTERVAL = 86_400  # seconds — once per day


def _run_purges() -> None:
    """Retention pass. Each purge is isolated so one failure keeps the other."""
    try:
        purge_old_runs()
    except Exception:
        log.exception("purge_old_runs failed, skipping this cycle")
    try:
        purge_expired_downloads()
    except Exception:
        log.exception("purge_expired_downloads failed, skipping this cycle")


def tick() -> bool:
    """One poll cycle. Returns True if a job was processed, False if idle."""
    return pipeline_tick()


def loop(poll_interval: float = 30.0) -> None:  # pragma: no cover - driven by tick() in tests
    try:
        concurrency = max(1, min(int(os.getenv("SCHEDULER_CONCURRENCY", "1")), 20))
    except ValueError:
        log.warning("SCHEDULER_CONCURRENCY is not a valid integer, defaulting to 1")
        concurrency = 1
    log.info("scheduler loop starting, poll_interval=%gs concurrency=%d", poll_interval, concurrency)
    reap_orphans()
    _run_purges()
    last_purge = time.monotonic()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        pending: set[Future] = set()
        while True:
            done = {f for f in pending if f.done()}
            for f in done:
                try:
                    f.result()
                except Exception:
                    log.exception("pipeline_tick raised")
            pending -= done

            if time.monotonic() - last_purge >= _PURGE_INTERVAL:
                _run_purges()
                last_purge = time.monotonic()

            slots = concurrency - len(pending)
            for _ in range(slots):
                pending.add(executor.submit(pipeline_tick))

            time.sleep(poll_interval)
