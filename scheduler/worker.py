"""Scheduler entry points.

tick() and loop() are called by scheduler/main.py.
All upload execution is delegated to pipeline_worker.
"""
from __future__ import annotations

import logging
import time

from scheduler.pipeline_worker import pipeline_tick, reap_orphans

log = logging.getLogger("scheduler.worker")


def tick() -> bool:
    """One poll cycle. Returns True if a job was processed, False if idle."""
    return pipeline_tick()


def loop(poll_interval: float = 30.0) -> None:  # pragma: no cover - driven by tick() in tests
    log.info("scheduler loop starting, poll_interval=%ss", poll_interval)
    reap_orphans()
    while True:
        try:
            busy = tick()
        except Exception:
            log.exception("tick failed")
            busy = False
        if not busy:
            time.sleep(poll_interval)
