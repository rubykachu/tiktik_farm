"""Scheduler entry points.

tick() and loop() are called by scheduler/main.py.
All upload execution is delegated to pipeline_worker.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor

from api.services.manual_download import purge_expired as purge_expired_downloads
from scheduler.discovery_worker import discovery_tick, reap_orphan_scan_runs
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
    reap_orphan_scan_runs()
    _run_purges()
    last_purge = time.monotonic()

    # Discovery gets its own single-worker executor, separate from the
    # pipeline pool: pipeline uploads are the revenue path and must never
    # wait on a multi-minute scan holding a pipeline concurrency slot.
    with ThreadPoolExecutor(max_workers=concurrency) as executor, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery") as discovery_executor:
        pending: set[Future] = set()
        discovery_future: Future | None = None
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

            if discovery_future is not None and discovery_future.done():
                try:
                    discovery_future.result()
                except Exception:
                    log.exception("discovery_tick raised")
                discovery_future = None
            if discovery_future is None:
                discovery_future = discovery_executor.submit(discovery_tick)

            slots = concurrency - len(pending)
            for _ in range(slots):
                pending.add(executor.submit(pipeline_tick))

            time.sleep(poll_interval)
