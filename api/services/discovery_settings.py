"""Read/write helpers for channel-discovery scoring thresholds stored in
AppSetting. Single source for every tunable used by
api.services.channel_discovery — nothing here is hardcoded at the call site.
Defaults are seeded by api.db_migrations.seed_app_settings.

Market (gl/hl) and the keyword set are per-scan search intent, not a
standing setting — they're sent with every POST /discovery/scan-now request
instead of living here.
"""
from __future__ import annotations

import api.db as _api_db
from api.db import now_utc
from api.models import AppSetting
from sqlmodel import Session

_DEFAULTS = {
    "discovery.min_subscribers": 100,
    "discovery.silent_months": 6,
    "discovery.target_days_per_video": 1.0,
    "discovery.view_score_reference": 1_000_000,
}


def _get_int(key: str) -> int:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, key)
        if row is None:
            return int(_DEFAULTS[key])
        try:
            return int(row.value)
        except ValueError:
            return int(_DEFAULTS[key])


def _get_float(key: str) -> float:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, key)
        if row is None:
            return float(_DEFAULTS[key])
        try:
            return float(row.value)
        except ValueError:
            return float(_DEFAULTS[key])


def _set(key: str, value: str) -> None:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
            row.updated_at = now_utc()
        s.add(row)
        s.commit()


def get_min_subscribers() -> int:
    return _get_int("discovery.min_subscribers")


def get_silent_months() -> int:
    return _get_int("discovery.silent_months")


def get_target_days_per_video() -> float:
    return _get_float("discovery.target_days_per_video")


def get_view_score_reference() -> int:
    return _get_int("discovery.view_score_reference")


def set_setting(key: str, value: str) -> None:
    if key not in _DEFAULTS:
        raise KeyError(f"unknown discovery setting: {key}")
    _set(key, value)
