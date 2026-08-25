"""Tests for /api/discovery — settings, keywords, candidates, scan status."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from api.middleware.origin import LOCAL_ORIGIN
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryScanRun
from api.services import channel_discovery as cd

HEADERS_OK = {"Origin": LOCAL_ORIGIN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    from tiktok_uploader.Config import Config
    Config._instance = None

    from api import db as _db
    from api.services import secrets as _secrets
    _secrets._reset_for_tests()

    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    from api.db_migrations import apply_indexes, apply_schema_patches, seed_app_settings
    apply_schema_patches(eng)
    apply_indexes(eng)
    seed_app_settings(eng)

    from api.main import create_app
    yield TestClient(create_app())
    _secrets._reset_for_tests()


class TestGetSettings:
    def test_defaults(self, client):
        r = client.get("/api/discovery/settings", headers=HEADERS_OK)
        assert r.status_code == 200
        body = r.json()
        assert body["min_subscribers"] == 100
        assert body["silent_months"] == 6
        assert body["target_days_per_video"] == 1.0
        assert body["market_gl"] == "US"
        assert body["market_hl"] == "en"


class TestPatchSettings:
    def test_partial_update(self, client):
        r = client.patch(
            "/api/discovery/settings",
            json={"target_days_per_video": 2.0, "market_gl": "JP"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_days_per_video"] == 2.0
        assert body["market_gl"] == "JP"
        assert body["min_subscribers"] == 100  # untouched

    def test_rejects_zero_target(self, client):
        r = client.patch(
            "/api/discovery/settings", json={"target_days_per_video": 0}, headers=HEADERS_OK
        )
        assert r.status_code == 422

    def test_requires_origin_header(self, client):
        r = client.patch("/api/discovery/settings", json={"min_subscribers": 500})
        assert r.status_code == 403


class TestKeywords:
    def test_create_and_list(self, client):
        r = client.post("/api/discovery/keywords", json={"keyword": "nhảy thiếu nhi"}, headers=HEADERS_OK)
        assert r.status_code == 201, r.text
        assert r.json()["keyword"] == "nhảy thiếu nhi"
        assert r.json()["last_scanned_at"] is None

        r2 = client.get("/api/discovery/keywords", headers=HEADERS_OK)
        assert len(r2.json()) == 1

    def test_duplicate_rejected(self, client):
        client.post("/api/discovery/keywords", json={"keyword": "kw"}, headers=HEADERS_OK)
        r = client.post("/api/discovery/keywords", json={"keyword": "kw"}, headers=HEADERS_OK)
        assert r.status_code == 409

    def test_delete(self, client):
        created = client.post(
            "/api/discovery/keywords", json={"keyword": "kw"}, headers=HEADERS_OK
        ).json()
        r = client.delete(f"/api/discovery/keywords/{created['id']}", headers=HEADERS_OK)
        assert r.status_code == 204
        assert client.get("/api/discovery/keywords", headers=HEADERS_OK).json() == []

    def test_delete_missing_404(self, client):
        r = client.delete("/api/discovery/keywords/9999", headers=HEADERS_OK)
        assert r.status_code == 404


class TestKeywordSuggestions:
    def test_calls_service_with_market_settings(self, client):
        with patch.object(cd, "suggest_keywords", return_value=["a", "b"]) as mock_suggest:
            r = client.get("/api/discovery/keyword-suggestions", params={"seed": "kids dance"}, headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json() == {"suggestions": ["a", "b"]}
        mock_suggest.assert_called_once_with("kids dance", "US", "en")

    def test_blank_seed_returns_empty_without_calling_service(self, client):
        with patch.object(cd, "suggest_keywords") as mock_suggest:
            r = client.get("/api/discovery/keyword-suggestions", params={"seed": "   "}, headers=HEADERS_OK)
        assert r.json() == {"suggestions": []}
        mock_suggest.assert_not_called()


class TestCandidates:
    def _add_candidate(self, session, **overrides):
        defaults = dict(
            channel_id="UC1",
            channel_url="https://www.youtube.com/@c1",
            channel_title="Channel 1",
            found_via_keyword="kw",
            score=50.0,
            status="new",
        )
        defaults.update(overrides)
        row = DiscoveryCandidate(**defaults)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    def test_lists_only_new_ordered_by_score_desc(self, client):
        from api import db as _db

        with Session(_db.engine) as s:
            self._add_candidate(s, channel_id="UC-low", score=10.0)
            self._add_candidate(s, channel_id="UC-high", score=90.0)
            self._add_candidate(s, channel_id="UC-skipped", score=99.0, status="skipped")

        r = client.get("/api/discovery/candidates", headers=HEADERS_OK)
        assert r.status_code == 200
        ids = [row["channel_id"] for row in r.json()]
        assert ids == ["UC-high", "UC-low"]

    def test_skip(self, client):
        from api import db as _db

        with Session(_db.engine) as s:
            row = self._add_candidate(s)
            candidate_id = row.id

        r = client.post(f"/api/discovery/candidates/{candidate_id}/skip", headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json()["status"] == "skipped"
        assert client.get("/api/discovery/candidates", headers=HEADERS_OK).json() == []

    def test_skip_missing_404(self, client):
        r = client.post("/api/discovery/candidates/9999/skip", headers=HEADERS_OK)
        assert r.status_code == 404

    def test_mark_added(self, client):
        from api import db as _db

        with Session(_db.engine) as s:
            pipeline = ChannelPipeline(
                name="p",
                channel_url="https://www.youtube.com/@c1",
                fetch_count=1,
                interval_value=1,
                interval_unit="days",
                first_fire_at=_db.now_utc(),
                next_fire_at=_db.now_utc(),
                caption_template="{title}",
            )
            s.add(pipeline)
            s.commit()
            s.refresh(pipeline)
            candidate = self._add_candidate(s)
            candidate_id, pipeline_id = candidate.id, pipeline.id

        r = client.post(
            f"/api/discovery/candidates/{candidate_id}/mark-added",
            json={"pipeline_id": pipeline_id},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "added"
        assert body["added_pipeline_id"] == pipeline_id
        # Marked-added candidates also drop out of the review list.
        assert client.get("/api/discovery/candidates", headers=HEADERS_OK).json() == []

    def test_mark_added_missing_pipeline_422(self, client):
        from api import db as _db

        with Session(_db.engine) as s:
            candidate = self._add_candidate(s)
            candidate_id = candidate.id

        r = client.post(
            f"/api/discovery/candidates/{candidate_id}/mark-added",
            json={"pipeline_id": 9999},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422


class TestScanStatus:
    def test_none_when_no_runs_yet(self, client):
        r = client.get("/api/discovery/scan-status", headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json() is None

    def test_latest_run_parsed(self, client):
        from api import db as _db

        with Session(_db.engine) as s:
            run = DiscoveryScanRun(
                keywords_json='["a", "b"]', channels_total=10, channels_done=3, status="running"
            )
            s.add(run)
            s.commit()

        r = client.get("/api/discovery/scan-status", headers=HEADERS_OK)
        body = r.json()
        assert body["keywords"] == ["a", "b"]
        assert body["channels_total"] == 10
        assert body["channels_done"] == 3
        assert body["status"] == "running"


class TestScanNow:
    def test_no_keywords_422(self, client):
        r = client.post("/api/discovery/scan-now", headers=HEADERS_OK)
        assert r.status_code == 422

    def test_queues_request(self, client):
        client.post("/api/discovery/keywords", json={"keyword": "kw"}, headers=HEADERS_OK)
        r = client.post("/api/discovery/scan-now", headers=HEADERS_OK)
        assert r.status_code == 200, r.text
        assert r.json() == {"queued": True}

        from api.services import channel_discovery as cd
        assert cd.is_run_now_requested() is True

    def test_already_running_409(self, client):
        from api import db as _db

        client.post("/api/discovery/keywords", json={"keyword": "kw"}, headers=HEADERS_OK)
        with Session(_db.engine) as s:
            s.add(DiscoveryScanRun(status="running"))
            s.commit()

        r = client.post("/api/discovery/scan-now", headers=HEADERS_OK)
        assert r.status_code == 409
