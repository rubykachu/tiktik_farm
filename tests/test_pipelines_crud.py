"""Tests for /api/pipelines CRUD."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HEADERS_OK = {"Origin": "http://127.0.0.1:3000"}


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

    def test_400_empty_accounts(self, client):
        r = client.post(
            "/api/pipelines",
            json=_valid_payload(account_id=1, account_ids=[]),
            headers=HEADERS_OK,
        )
        assert r.status_code in (400, 422)

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
        rows = r2.json()
        assert len(rows) == 1
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
