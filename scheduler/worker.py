"""Scheduler entry points.

tick() and loop() are called by scheduler/main.py.
All upload execution is delegated to pipeline_worker.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor

from scheduler.pipeline_worker import pipeline_tick, purge_old_runs, reap_orphans

log = logging.getLogger("scheduler.worker")


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
    purge_old_runs()

    _PURGE_INTERVAL = 86_400  # once per day
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
                purge_old_runs()
                last_purge = time.monotonic()

            slots = concurrency - len(pending)
            for _ in range(slots):
                pending.add(executor.submit(pipeline_tick))

            time.sleep(poll_interval)
