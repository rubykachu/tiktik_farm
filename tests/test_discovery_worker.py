"""Tests for scheduler.discovery_worker — the scheduler-side driver that
decides WHEN to scan and records progress, delegating the actual scan to
api.services.channel_discovery.run_scan (mocked here)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from api import db as _db
from api.db_migrations import seed_app_settings
from api.models import DiscoveryCandidate, DiscoveryKeyword, DiscoveryScanRun
from api.services import channel_discovery as cd
from api.services import discovery_settings as settings
from scheduler import discovery_worker as dw


@pytest.fixture
def engine(tmp_path):
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    seed_app_settings(eng)
    return eng


def _add_keyword(keyword: str, last_scanned_at=None) -> DiscoveryKeyword:
    with Session(_db.engine) as s:
        row = DiscoveryKeyword(keyword=keyword, last_scanned_at=last_scanned_at)
        s.add(row)
        s.commit()
        s.refresh(row)
        s.expunge(row)
        return row


class TestIsDue:
    def test_due_when_never_scanned(self, engine):
        assert dw._is_due() is True

    def test_not_due_right_after_a_scan(self, engine):
        with Session(_db.engine) as s:
            s.add(DiscoveryScanRun(status="done"))
            s.commit()
        assert dw._is_due() is False

    def test_due_after_interval_elapses(self, engine):
        settings.set_setting("discovery.scans_per_day", "3")  # every 8h
        with Session(_db.engine) as s:
            run = DiscoveryScanRun(status="done")
            run.started_at = _db.now_utc() - timedelta(hours=9)
            s.add(run)
            s.commit()
        assert dw._is_due() is True

    def test_hard_floor_overrides_a_misconfigured_high_scans_per_day(self, engine):
        # scans_per_day=100 asks for a scan every ~14 min; the hard floor
        # (>= 4h between scans) must win regardless of the setting.
        settings.set_setting("discovery.scans_per_day", "100")
        with Session(_db.engine) as s:
            run = DiscoveryScanRun(status="done")
            run.started_at = _db.now_utc() - timedelta(hours=1)
            s.add(run)
            s.commit()
        assert dw._is_due() is False


class TestPickKeywords:
    def test_never_scanned_keywords_come_first(self, engine):
        _add_keyword("old", last_scanned_at=_db.now_utc() - timedelta(days=1))
        _add_keyword("never-scanned")
        settings.set_setting("discovery.keywords_per_scan", "1")
        picked = dw._pick_keywords()
        assert [k.keyword for k in picked] == ["never-scanned"]

    def test_respects_keywords_per_scan_limit(self, engine):
        for i in range(5):
            _add_keyword(f"kw{i}")
        settings.set_setting("discovery.keywords_per_scan", "2")
        assert len(dw._pick_keywords()) == 2

    def test_empty_when_no_keywords_approved(self, engine):
        assert dw._pick_keywords() == []

    def test_hard_cap_overrides_a_misconfigured_high_keywords_per_scan(self, engine):
        for i in range(10):
            _add_keyword(f"kw{i}")
        settings.set_setting("discovery.keywords_per_scan", "9999")
        assert len(dw._pick_keywords()) == dw._MAX_KEYWORDS_PER_SCAN


class TestDiscoveryTick:
    def test_not_due_returns_false_without_running_scan(self, engine):
        with Session(_db.engine) as s:
            s.add(DiscoveryScanRun(status="done"))
            s.commit()
        with patch.object(cd, "run_scan") as mock_scan:
            assert dw.discovery_tick() is False
        mock_scan.assert_not_called()

    def test_no_keywords_returns_false(self, engine):
        with patch.object(cd, "run_scan") as mock_scan:
            assert dw.discovery_tick() is False
        mock_scan.assert_not_called()

    def test_runs_scan_and_records_progress(self, engine):
        kw = _add_keyword("nhảy thiếu nhi")
        summary = cd.ScanSummary(channels_total=5, channels_scanned=5, candidates_found=2, candidates_rejected=3)

        def fake_run_scan(keywords, progress_cb=None):
            assert keywords == ["nhảy thiếu nhi"]
            if progress_cb:
                progress_cb(0, 5)
                progress_cb(5, 5)
            return summary

        with patch.object(cd, "run_scan", side_effect=fake_run_scan):
            assert dw.discovery_tick() is True

        with Session(_db.engine) as s:
            run = s.exec(select(DiscoveryScanRun)).first()
            refreshed_kw = s.get(DiscoveryKeyword, kw.id)

        assert run.status == "done"
        assert run.channels_total == 5
        assert run.channels_done == 5
        assert run.candidates_found == 2
        assert refreshed_kw.last_scanned_at is not None

    def test_scan_failure_finalizes_run_as_error(self, engine):
        _add_keyword("kw")
        with patch.object(cd, "run_scan", side_effect=RuntimeError("yt-dlp exploded")):
            assert dw.discovery_tick() is True

        with Session(_db.engine) as s:
            run = s.exec(select(DiscoveryScanRun)).first()
        assert run.status == "error"
        assert "yt-dlp exploded" in run.error_text

    def test_failed_scan_does_not_mark_keyword_scanned(self, engine):
        kw = _add_keyword("kw")
        with patch.object(cd, "run_scan", side_effect=RuntimeError("boom")):
            dw.discovery_tick()
        with Session(_db.engine) as s:
            refreshed = s.get(DiscoveryKeyword, kw.id)
        # Left unscanned so a transient failure doesn't push it to the back
        # of the rotation.
        assert refreshed.last_scanned_at is None


class TestReapOrphanScanRuns:
    def test_stale_running_row_marked_errored(self, engine):
        with Session(_db.engine) as s:
            run = DiscoveryScanRun(status="running")
            run.updated_at = _db.now_utc() - timedelta(minutes=20)
            s.add(run)
            s.commit()
            run_id = run.id

        count = dw.reap_orphan_scan_runs()

        assert count == 1
        with Session(_db.engine) as s:
            reaped = s.get(DiscoveryScanRun, run_id)
        assert reaped.status == "error"
        assert reaped.error_text == "worker crashed"
        assert reaped.finished_at is not None

    def test_recently_updated_running_row_left_alone(self, engine):
        with Session(_db.engine) as s:
            run = DiscoveryScanRun(status="running")
            s.add(run)
            s.commit()
            run_id = run.id

        count = dw.reap_orphan_scan_runs()

        assert count == 0
        with Session(_db.engine) as s:
            untouched = s.get(DiscoveryScanRun, run_id)
        assert untouched.status == "running"

    def test_already_finished_rows_are_ignored(self, engine):
        with Session(_db.engine) as s:
            run = DiscoveryScanRun(status="done")
            run.updated_at = _db.now_utc() - timedelta(hours=5)
            s.add(run)
            s.commit()

        assert dw.reap_orphan_scan_runs() == 0
