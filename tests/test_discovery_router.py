"""Tests for /api/discovery — settings, keywords, scan-now/history,
per-channel actions. Background scan execution (api.services.channel_discovery
.run_scan) is mocked; starlette's TestClient runs BackgroundTasks
synchronously before returning the response, so a mocked call is observable
right after the request completes."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from api.middleware.origin import LOCAL_ORIGIN
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryScanResult, DiscoveryScanRun
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
        assert body == {
            "min_subscribers": 100,
            "silent_months": 6,
            "target_days_per_video": 1.0,
            "view_score_reference": 1_000_000,
        }


class TestPatchSettings:
    def test_partial_update(self, client):
        r = client.patch(
            "/api/discovery/settings", json={"target_days_per_video": 2.0}, headers=HEADERS_OK
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_days_per_video"] == 2.0
        assert body["min_subscribers"] == 100  # untouched

    def test_rejects_zero_target(self, client):
        r = client.patch(
            "/api/discovery/settings", json={"target_days_per_video": 0}, headers=HEADERS_OK
        )
        assert r.status_code == 422

    def test_requires_origin_header(self, client):
        r = client.patch("/api/discovery/settings", json={"min_subscribers": 500})
        assert r.status_code == 403


class TestKeywordSuggestions:
    def test_calls_service_with_given_market(self, client):
        with patch.object(cd, "suggest_keywords", return_value=["a", "b"]) as mock_suggest:
            r = client.get(
                "/api/discovery/keyword-suggestions",
                params={"seed": "kids dance", "gl": "JP", "hl": "ja"},
                headers=HEADERS_OK,
            )
        assert r.status_code == 200
        assert r.json() == {"suggestions": ["a", "b"]}
        mock_suggest.assert_called_once_with("kids dance", "JP", "ja")

    def test_blank_seed_returns_empty_without_calling_service(self, client):
        with patch.object(cd, "suggest_keywords") as mock_suggest:
            r = client.get(
                "/api/discovery/keyword-suggestions",
                params={"seed": "   ", "gl": "US", "hl": "en"},
                headers=HEADERS_OK,
            )
        assert r.json() == {"suggestions": []}
        mock_suggest.assert_not_called()


class TestScanNow:
    def test_starts_a_scan(self, client):
        with patch.object(cd, "run_scan") as mock_run:
            r = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "running"
        mock_run.assert_called_once()
        assert mock_run.call_args.args[1:] == (["kw"], "US", "en")

    def test_rejects_more_than_five_keywords(self, client):
        r = client.post(
            "/api/discovery/scan-now",
            json={"keywords": ["a", "b", "c", "d", "e", "f"], "market_gl": "US", "market_hl": "en"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    def test_rejects_empty_keywords(self, client):
        r = client.post(
            "/api/discovery/scan-now", json={"keywords": [], "market_gl": "US", "market_hl": "en"}, headers=HEADERS_OK
        )
        assert r.status_code == 422

    def test_rescanning_same_set_reuses_same_run_id(self, client):
        with patch.object(cd, "run_scan"):
            r1 = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )
            from api import db as _db

            with Session(_db.engine) as s:
                run = s.get(DiscoveryScanRun, r1.json()["scan_run_id"])
                run.status = "done"
                s.add(run)
                s.commit()

            r2 = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["KW"], "market_gl": "US", "market_hl": "en"},  # same set, different case
                headers=HEADERS_OK,
            )
        assert r2.json()["scan_run_id"] == r1.json()["scan_run_id"]

    def test_reruns_delete_old_results(self, client):
        from api import db as _db

        with patch.object(cd, "run_scan"):
            r1 = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )
            run_id = r1.json()["scan_run_id"]
            with Session(_db.engine) as s:
                s.add(DiscoveryScanResult(scan_run_id=run_id, channel_id="UC-old", channel_url="u", channel_title="t", found_via_keyword="kw"))
                run = s.get(DiscoveryScanRun, run_id)
                run.status = "done"
                s.add(run)
                s.commit()

            client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )

        with Session(_db.engine) as s:
            remaining = s.exec(
                __import__("sqlmodel").select(DiscoveryScanResult).where(DiscoveryScanResult.scan_run_id == run_id)
            ).all()
        assert remaining == []

    def test_join_in_flight_run_instead_of_duplicating(self, client):
        with patch.object(cd, "run_scan") as mock_run:
            r1 = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )
            # First call's background task ran synchronously already (mocked,
            # so status is still "running" — force it to simulate a still-in-flight scan).
            from api import db as _db

            with Session(_db.engine) as s:
                run = s.get(DiscoveryScanRun, r1.json()["scan_run_id"])
                run.status = "running"
                s.add(run)
                s.commit()

            r2 = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )
        assert r2.json()["scan_run_id"] == r1.json()["scan_run_id"]
        assert mock_run.call_count == 1  # second click did not start a new scan


class TestScanRuns:
    def _create_run(self, client) -> int:
        with patch.object(cd, "run_scan"):
            r = client.post(
                "/api/discovery/scan-now",
                json={"keywords": ["kw1", "kw2"], "market_gl": "US", "market_hl": "en"},
                headers=HEADERS_OK,
            )
        return r.json()["scan_run_id"]

    def test_list_history(self, client):
        self._create_run(client)
        r = client.get("/api/discovery/scan-runs", headers=HEADERS_OK)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert sorted(rows[0]["keywords"]) == ["kw1", "kw2"]
        assert rows[0]["market_gl"] == "US"

    def test_get_single_run(self, client):
        run_id = self._create_run(client)
        r = client.get(f"/api/discovery/scan-runs/{run_id}", headers=HEADERS_OK)
        assert r.status_code == 200
        assert r.json()["id"] == run_id

    def test_get_missing_run_404(self, client):
        r = client.get("/api/discovery/scan-runs/9999", headers=HEADERS_OK)
        assert r.status_code == 404

    def test_results_scoped_to_one_run_and_joined_with_status(self, client):
        from api import db as _db

        run_a = self._create_run(client)
        with Session(_db.engine) as s:
            run_b = DiscoveryScanRun(keywords_key="other", keywords_json="[]", market_gl="US", market_hl="en", status="done")
            s.add(run_b)
            s.commit()
            s.refresh(run_b)
            s.add(
                DiscoveryScanResult(
                    scan_run_id=run_a, channel_id="UC-a", channel_url="https://x/@a", channel_title="A", found_via_keyword="kw", score=50
                )
            )
            s.add(
                DiscoveryScanResult(
                    scan_run_id=run_b.id, channel_id="UC-b", channel_url="https://x/@b", channel_title="B", found_via_keyword="kw", score=60
                )
            )
            s.add(DiscoveryCandidate(channel_id="UC-a", channel_url="https://x/@a", channel_title="A", status="skipped"))
            s.commit()

        r = client.get(f"/api/discovery/scan-runs/{run_a}/results", headers=HEADERS_OK)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1  # only run_a's result, not run_b's
        assert rows[0]["channel_id"] == "UC-a"
        assert rows[0]["status"] == "skipped"

    def test_results_missing_run_404(self, client):
        r = client.get("/api/discovery/scan-runs/9999/results", headers=HEADERS_OK)
        assert r.status_code == 404


class TestChannelActions:
    def test_skip_creates_candidate_row(self, client):
        r = client.post(
            "/api/discovery/channels/UC-x/skip",
            json={"channel_url": "https://x/@x", "channel_title": "X"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"channel_id": "UC-x", "status": "skipped", "added_pipeline_id": None}

    def test_mark_added(self, client):
        from api import db as _db

        with Session(_db.engine) as s:
            pipeline = ChannelPipeline(
                name="p",
                channel_url="https://x/@x",
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
            pipeline_id = pipeline.id

        r = client.post(
            "/api/discovery/channels/UC-x/mark-added",
            json={"channel_url": "https://x/@x", "channel_title": "X", "pipeline_id": pipeline_id},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"channel_id": "UC-x", "status": "added", "added_pipeline_id": pipeline_id}

    def test_mark_added_missing_pipeline_422(self, client):
        r = client.post(
            "/api/discovery/channels/UC-x/mark-added",
            json={"channel_url": "https://x/@x", "channel_title": "X", "pipeline_id": 9999},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422
