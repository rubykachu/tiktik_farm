"""Read/write helpers for channel-discovery thresholds stored in AppSetting.

Single source for every tunable used by api.services.channel_discovery and
the scheduler's discovery tick — nothing here is hardcoded at the call site.
Defaults are seeded by api.db_migrations.seed_app_settings.
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
    "discovery.scans_per_day": 3,
    "discovery.keywords_per_scan": 3,
    "discovery.view_score_reference": 1_000_000,
}
_STR_DEFAULTS = {
    "discovery.market_gl": "US",
    "discovery.market_hl": "en",
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


def _get_str(key: str) -> str:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, key)
        return row.value if row and row.value else _STR_DEFAULTS[key]


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


def get_scans_per_day() -> int:
    return _get_int("discovery.scans_per_day")


def get_keywords_per_scan() -> int:
    return _get_int("discovery.keywords_per_scan")


def get_view_score_reference() -> int:
    return _get_int("discovery.view_score_reference")


def get_market() -> tuple[str, str]:
    """Returns (gl, hl) — country + language for search/autocomplete."""
    return _get_str("discovery.market_gl"), _get_str("discovery.market_hl")


def set_setting(key: str, value: str) -> None:
    if key not in _DEFAULTS and key not in _STR_DEFAULTS:
        raise KeyError(f"unknown discovery setting: {key}")
    _set(key, value)
