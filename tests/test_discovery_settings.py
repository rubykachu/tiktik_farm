"""Tests for api.services.discovery_settings — the single source of truth
for channel-discovery scoring thresholds (min subscribers, cadence target,
etc.). Market and the keyword set are per-scan parameters now, not settings
— see tests/test_discovery_router.py's scan-now tests instead."""
from __future__ import annotations

import pytest

from api import db as _db
from api.db_migrations import seed_app_settings
from api.services import discovery_settings as ds


@pytest.fixture
def engine(tmp_path):
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    seed_app_settings(eng)
    return eng


class TestDefaults:
    def test_seeded_defaults(self, engine):
        assert ds.get_min_subscribers() == 100
        assert ds.get_silent_months() == 6
        assert ds.get_target_days_per_video() == 1.0
        assert ds.get_view_score_reference() == 1_000_000

    def test_falls_back_when_row_absent(self, engine):
        # Nothing seeded — fresh engine without seed_app_settings.
        eng2 = _db.make_engine("sqlite:///:memory:")
        _db.override_engine(eng2)
        _db.init_db()
        assert ds.get_min_subscribers() == 100
        _db.override_engine(engine)


class TestSetSetting:
    def test_round_trip(self, engine):
        ds.set_setting("discovery.min_subscribers", "300")
        assert ds.get_min_subscribers() == 300

    def test_unknown_key_rejected(self, engine):
        with pytest.raises(KeyError):
            ds.set_setting("discovery.not_a_real_key", "1")

    def test_corrupt_value_falls_back_to_default(self, engine):
        ds.set_setting("discovery.min_subscribers", "not-a-number")
        assert ds.get_min_subscribers() == 100
