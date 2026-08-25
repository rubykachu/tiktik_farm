"""Tests for /api/pipelines CRUD."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from api.middleware.origin import LOCAL_ORIGIN

HEADERS_OK = {"Origin": LOCAL_ORIGIN}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    from tiktok_uploader.Config import Config
    Config._instance = None
    from api import db as _db
    from api.main import create_app
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    from api.db_migrations import apply_indexes, apply_schema_patches
    apply_schema_patches(eng)
    apply_indexes(eng)
    return TestClient(create_app())


def _make_account(client, username="acct1"):
    """Insert an Account row directly via the DB session."""
    from sqlmodel import Session
    from api import db as _db
    from api.models import Account

    with Session(_db.engine) as s:
        a = Account(username=username, cookie_path=f"/x/{username}.cookie")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _valid_payload(account_id: int, **overrides):
    base = {
        "name": "Test Pipeline",
        "channel_url": "https://youtube.com/@cubez/videos",
        "fetch_count": 3,
        "interval_value": 1,
        "interval_unit": "hours",
        "first_fire_at": (_now() + timedelta(hours=1)).isoformat(),
        "caption_template": "{title} #cubez",
        "upload_options": {
            "allow_comment": 1,
            "allow_duet": 0,
            "allow_stitch": 0,
            "visibility_type": 0,
        },
        "account_ids": [account_id],
    }
    base.update(overrides)
    return base


# ----------------- POST -----------------------------------------------------

class TestCreate:
    def test_201_happy_path(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Test Pipeline"
        assert body["state"] == "active"
        # bot_token MUST NOT be in response.
        assert "telegram_bot_token" not in body
        assert body["telegram_bot_token_set"] is False
        # next_fire_at == first_fire_at on create.
        assert body["next_fire_at"] == body["first_fire_at"]
        assert body["upload_delay_minutes"] == 0

    def test_400_unknown_template_var(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, caption_template="{title} {foo}"),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)
        assert "foo" in r.text

    def test_400_non_youtube_channel_url(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, channel_url="https://evil.com/@x/videos"),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_400_bad_telegram_token(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="not-a-valid-token",
                telegram_chat_id="123",
            ),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_400_telegram_chat_id_username(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="1234567:abcdefghijklmnopqrstuvwxyz_AAA",
                telegram_chat_id="@mychannel",
            ),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_400_minutes_interval_below_2(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, interval_unit="minutes", interval_value=1),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_201_minutes_interval_at_2(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, interval_unit="minutes", interval_value=2),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201

    def test_400_fetch_count_zero(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, fetch_count=0),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_400_fetch_count_over_20(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, fetch_count=21),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_201_with_upload_delay_minutes(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, upload_delay_minutes=5),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        assert r.json()["upload_delay_minutes"] == 5

    def test_422_upload_delay_minutes_over_30(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, upload_delay_minutes=31),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

    def test_201_empty_accounts(self, client):
        """No TikTok account = download-only pipeline; the run notifies instead."""
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(account_id=1, account_ids=[]),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201
        assert r.json()["account_ids_json"] == "[]"

    def test_400_account_id_does_not_exist(self, client):
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(account_id=99999),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)


# ----------------- GET ------------------------------------------------------

class TestRead:
    def test_get_list_excludes_bot_token(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="1234567:abcdefghijklmnopqrstuvwxyz_AAA",
                telegram_chat_id="-100123",
            ),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201
        r2 = client.get("/api/pipelines")
        assert r2.status_code == 200
        body = r2.json()
        rows = body["items"]
        assert len(rows) == 1
        assert body["total"] == 1
        assert "telegram_bot_token" not in rows[0]
        assert rows[0]["telegram_bot_token_set"] is True

    def test_get_one_404(self, client):
        r = client.get("/api/pipelines/9999")
        assert r.status_code == 404

    def test_get_one_excludes_bot_token(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="555:abcdefghij1234567890ABCDEFGHIJKL",
                telegram_chat_id="-1001234567890",
            ),
            headers=HEADERS_OK,
        )
        pid = r.json()["id"]
        body = client.get(f"/api/pipelines/{pid}").json()
        assert "telegram_bot_token" not in body
        assert body["telegram_bot_token_set"] is True


# ----------------- PATCH ----------------------------------------------------

class TestPatch:
    def test_patch_pause_clears_run_now_queue(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        client.post(f"/api/pipelines/{pid}/run-now", headers=HEADERS_OK)
        assert client.get(f"/api/pipelines/{pid}").json()["run_now_requested_at"] is not None
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"state": "paused"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json()["state"] == "paused"
        assert r.json()["run_now_requested_at"] is None

    def test_patch_state_paused(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"state": "paused"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        assert r.json()["state"] == "paused"

    def test_patch_empty_bot_token_does_not_change_existing(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="123:abcdefghij1234567890ABCDEFGHIJKL",
                telegram_chat_id="100",
            ),
            headers=HEADERS_OK,
        ).json()["id"]

        client.patch(
            f"/api/pipelines/{pid}",
            json={"telegram_bot_token": ""},
            headers=HEADERS_OK,
        )
        body = client.get(f"/api/pipelines/{pid}").json()
        assert body["telegram_bot_token_set"] is True

    def test_patch_clear_bot_token(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="123:abcdefghij1234567890ABCDEFGHIJKL",
                telegram_chat_id="100",
            ),
            headers=HEADERS_OK,
        ).json()["id"]

        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"clear_bot_token": True},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        body = client.get(f"/api/pipelines/{pid}").json()
        assert body["telegram_bot_token_set"] is False

    def test_patch_422_chat_id_without_stored_bot(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"telegram_chat_id": "-1001234567890"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    def test_patch_422_clear_bot_token_with_chat_id_body(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_valid_payload(
                aid,
                telegram_bot_token="123:abcdefghij1234567890ABCDEFGHIJKL",
                telegram_chat_id="100",
            ),
            headers=HEADERS_OK,
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"clear_bot_token": True, "telegram_chat_id": "-100999"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422

    def test_patch_first_fire_at_active_updates_next_fire(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        new_start = (_now() + timedelta(days=2)).isoformat()
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"first_fire_at": new_start},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["first_fire_at"] is not None
        nxt = datetime.fromisoformat(body["next_fire_at"].replace("Z", "+00:00"))
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        assert nxt >= _now()

    def test_patch_first_fire_at_when_paused(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        client.patch(
            f"/api/pipelines/{pid}",
            json={"state": "paused"},
            headers=HEADERS_OK,
        )
        future = (_now() + timedelta(days=10)).isoformat()
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"first_fire_at": future},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "paused"

    def test_patch_upload_delay_minutes(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"upload_delay_minutes": 15},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json()["upload_delay_minutes"] == 15


# ----------------- DELETE ---------------------------------------------------

class TestListPagination:
    def _make(self, client, n: int) -> None:
        for i in range(n):
            aid = _make_account(client, username=f"pg{i}")
            client.post("/api/pipelines",
                        json=_valid_payload(aid, name=f"pipe {i:03d}"),
                        headers=HEADERS_OK)

    def test_defaults_to_the_configured_page_size(self, client):
        from api.pagination import PIPELINES_PER_PAGE

        body = client.get("/api/pipelines").json()
        assert body["size"] == PIPELINES_PER_PAGE
        assert body["page"] == 1

    def test_total_counts_every_pipeline_not_just_the_page(self, client):
        self._make(client, 3)

        body = client.get("/api/pipelines", params={"size": 2}).json()

        assert len(body["items"]) == 2
        assert body["total"] == 3

    def test_second_page_returns_the_remainder(self, client):
        self._make(client, 3)

        first = client.get("/api/pipelines", params={"size": 2, "page": 1}).json()
        second = client.get("/api/pipelines", params={"size": 2, "page": 2}).json()

        assert len(second["items"]) == 1
        ids = {p["id"] for p in first["items"]} & {p["id"] for p in second["items"]}
        assert ids == set()

    def test_total_respects_the_state_filter(self, client):
        self._make(client, 2)
        rows = client.get("/api/pipelines").json()["items"]
        client.patch(f"/api/pipelines/{rows[0]['id']}",
                     json={"state": "paused"}, headers=HEADERS_OK)

        body = client.get("/api/pipelines", params={"state": "paused"}).json()

        assert body["total"] == 1

    def test_page_past_the_end_is_empty_not_an_error(self, client):
        self._make(client, 1)

        body = client.get("/api/pipelines", params={"page": 99}).json()

        assert body["items"] == []
        assert body["total"] == 1

    def test_rejects_out_of_range_paging(self, client):
        assert client.get("/api/pipelines", params={"page": 0}).status_code == 422
        assert client.get("/api/pipelines", params={"size": 999}).status_code == 422


class TestLastRunSummary:
    """`state` says whether the operator switched a pipeline on; it says
    nothing about whether the thing works. The list needs both."""

    _seq = 0

    def _pipeline_with_run(self, client, **run_kw):
        from api import db as _db
        from api.models import PipelineRun

        TestLastRunSummary._seq += 1
        n = TestLastRunSummary._seq
        aid = _make_account(client, username=f"acct_lr{n}")
        pid = client.post("/api/pipelines", json=_valid_payload(aid, name=f"pipe {n}"),
                          headers=HEADERS_OK).json()["id"]
        with Session(_db.engine) as s:
            s.add(PipelineRun(pipeline_id=pid, started_at=_now(),
                              trigger_reason="scheduled", **run_kw))
            s.commit()
        return pid

    def _last_run(self, client, pid):
        rows = client.get("/api/pipelines").json()["items"]
        return next(r for r in rows if r["id"] == pid)["last_run"]

    def test_never_run_is_null(self, client):
        aid = _make_account(client)
        pid = client.post("/api/pipelines", json=_valid_payload(aid),
                          headers=HEADERS_OK).json()["id"]
        assert self._last_run(client, pid) is None

    def test_clean_run_is_ok(self, client):
        pid = self._pipeline_with_run(client, finished_at=_now(), uploaded_count=2)
        assert self._last_run(client, pid)["status"] == "ok"

    def test_all_uploads_failed_is_failed(self, client):
        pid = self._pipeline_with_run(client, finished_at=_now(), failed_count=2)
        assert self._last_run(client, pid)["status"] == "failed"

    def test_some_uploads_failed_is_partial(self, client):
        pid = self._pipeline_with_run(client, finished_at=_now(),
                                      uploaded_count=1, failed_count=1)
        assert self._last_run(client, pid)["status"] == "partial"

    def test_system_error_outranks_counts(self, client):
        pid = self._pipeline_with_run(client, finished_at=_now(),
                                      uploaded_count=1, error_text="low_disk")
        summary = self._last_run(client, pid)
        assert summary["status"] == "error"
        assert summary["error_text"] == "low_disk"

    def test_unfinished_run_is_running(self, client):
        pid = self._pipeline_with_run(client)
        assert self._last_run(client, pid)["status"] == "running"

    def test_reports_the_newest_run_only(self, client):
        from datetime import timedelta

        from api import db as _db
        from api.models import PipelineRun

        pid = self._pipeline_with_run(client, finished_at=_now(), failed_count=3)
        with Session(_db.engine) as s:
            s.add(PipelineRun(pipeline_id=pid, started_at=_now() + timedelta(hours=1),
                              finished_at=_now() + timedelta(hours=1),
                              trigger_reason="scheduled", uploaded_count=1))
            s.commit()

        assert self._last_run(client, pid)["status"] == "ok"

    def test_each_pipeline_gets_its_own_run(self, client):
        """One query serves every row; a join bug would smear them together."""
        good = self._pipeline_with_run(client, finished_at=_now(), uploaded_count=1)
        bad = self._pipeline_with_run(client, finished_at=_now(), failed_count=1)

        assert self._last_run(client, good)["status"] == "ok"
        assert self._last_run(client, bad)["status"] == "failed"


class TestDelete:
    def test_delete_204_when_not_locked(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        r = client.delete(f"/api/pipelines/{pid}", headers=HEADERS_OK)
        assert r.status_code in (200, 204)

    def test_delete_409_when_locked(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        # Simulate worker lock via direct DB update.
        from sqlmodel import Session, select
        from api import db as _db
        from api.models import ChannelPipeline
        with Session(_db.engine) as s:
            row = s.exec(
                select(ChannelPipeline).where(ChannelPipeline.id == pid)
            ).one()
            row.lock_until = _now() + timedelta(minutes=5)
            s.add(row)
            s.commit()
        r = client.delete(f"/api/pipelines/{pid}", headers=HEADERS_OK)
        assert r.status_code == 409


# ─── Window scheduling ───────────────────────────────────────────────────────


def _daily_payload(account_id: int, **overrides):
    """Valid payload with interval_unit=days (required for window fields)."""
    base = {
        "name": "Window Pipeline",
        "channel_url": "https://youtube.com/@cubez/videos",
        "fetch_count": 1,
        "interval_value": 1,
        "interval_unit": "days",
        "first_fire_at": (_now().replace(hour=0, minute=0, second=0, microsecond=0)
                          + timedelta(days=1)).isoformat(),
        "caption_template": "{title}",
        "upload_options": {"allow_comment": 1, "allow_duet": 0,
                           "allow_stitch": 0, "visibility_type": 0},
        "account_ids": [account_id],
    }
    base.update(overrides)
    return base


class TestWindowSchedulingAPI:
    # R1: create with valid window → 201, fields in response
    def test_create_with_window_returns_fields(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_daily_payload(aid, window_end_time="00:30", window_delay_minutes=5),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["window_end_time"] == "00:30"
        assert body["window_delay_minutes"] == 5

    # R1b: create without window → fields are null
    def test_create_without_window_fields_are_null(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_daily_payload(aid),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["window_end_time"] is None
        assert body["window_delay_minutes"] is None

    # R2: invalid window (end before start) → 422
    def test_create_window_end_before_start_422(self, client):
        aid = _make_account(client)
        ffa = (_now().replace(hour=1, minute=0, second=0, microsecond=0)
               + timedelta(days=1)).isoformat()
        r = client.post(
            "/api/pipelines",
            json=_daily_payload(aid, first_fire_at=ffa,
                                window_end_time="00:30", window_delay_minutes=5),
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text

    # R2b: window on minutes-interval pipeline → 422
    def test_create_window_on_minutes_interval_422(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json={**_daily_payload(aid), "interval_unit": "minutes",
                  "interval_value": 10,
                  "window_end_time": "00:30", "window_delay_minutes": 5},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text

    # R2c: asymmetric window (only end_time) → 422
    def test_create_asymmetric_window_422(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_daily_payload(aid, window_end_time="00:30"),
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text

    # R3: PATCH to add window → 200, fields persisted
    def test_patch_add_window_persists(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_daily_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"window_end_time": "00:30", "window_delay_minutes": 5},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["window_end_time"] == "00:30"
        assert body["window_delay_minutes"] == 5

    # R4: PATCH to clear window (both null) → 200, fields nulled
    def test_patch_clear_window(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_daily_payload(aid, window_end_time="00:30", window_delay_minutes=5),
            headers=HEADERS_OK,
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"window_end_time": None, "window_delay_minutes": None},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["window_end_time"] is None
        assert body["window_delay_minutes"] is None

    # Blocker 1 regression: PATCH window onto minutes-interval pipeline → 422
    def test_patch_window_on_minutes_interval_422(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json={**_daily_payload(aid), "interval_unit": "minutes", "interval_value": 10,
                  "window_end_time": None, "window_delay_minutes": None},
            headers=HEADERS_OK,
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"window_end_time": "00:30", "window_delay_minutes": 5},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text

    # Blocker 2 regression: PATCH only one window field → 422 (half-set state blocked)
    def test_patch_half_set_window_422(self, client):  # noqa: keep name
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_daily_payload(aid, window_end_time="00:30", window_delay_minutes=5),
            headers=HEADERS_OK,
        ).json()["id"]
        # Null only delay, leave end_time in DB
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"window_delay_minutes": None},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text


# ─── channel_description field ───────────────────────────────────────────────


class TestChannelDescription:
    def test_create_and_read_round_trip(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, channel_description="food review channel"),
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        assert r.json()["channel_description"] == "food review channel"

    def test_create_without_field_returns_null(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        )
        assert r.status_code == 201, r.text
        assert r.json()["channel_description"] is None

    def test_patch_set_value(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines", json=_valid_payload(aid), headers=HEADERS_OK
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"channel_description": "tech channel"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json()["channel_description"] == "tech channel"

    def test_patch_clear_to_none(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, channel_description="existing desc"),
            headers=HEADERS_OK,
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"channel_description": None},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json()["channel_description"] is None

    def test_patch_absent_preserves_existing(self, client):
        aid = _make_account(client)
        pid = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, channel_description="keep me"),
            headers=HEADERS_OK,
        ).json()["id"]
        r = client.patch(
            f"/api/pipelines/{pid}",
            json={"fetch_count": 5},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200, r.text
        assert r.json()["channel_description"] == "keep me"

    def test_post_over_max_length_422(self, client):
        aid = _make_account(client)
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(aid, channel_description="x" * 2001),
            headers=HEADERS_OK,
        )
        assert r.status_code == 422, r.text


class TestMigrationIdempotency:
    def test_apply_schema_patches_twice_no_error(self, client):
        """apply_schema_patches on a DB that already has all columns must not raise."""
        from api import db as _db
        from api.db_migrations import apply_schema_patches
        apply_schema_patches(_db.engine)  # second call — columns exist, silent no-op
