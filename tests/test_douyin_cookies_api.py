"""Tests for GET/POST/PUT/DELETE /api/douyin-cookies endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import api.db as _api_db
from api.db import make_engine, override_engine
from api.main import app  # noqa: F401 — registers all models in SQLModel.metadata
from api.models import DouyinCookie  # noqa: F401
from api.middleware.origin import LOCAL_ORIGIN


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from api.services import secrets as _sec
    _sec._reset_for_tests()
    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    _api_db.init_db()  # create_all + _run_migrations (matches production startup)
    yield engine
    override_engine(make_engine("sqlite:///"))
    _sec._reset_for_tests()


@pytest.fixture
def client():
    return TestClient(app, headers={"Origin": LOCAL_ORIGIN})


VALID_COOKIE_LIST = [
    {"name": "ttwid", "value": "abc123xyz"},
    {"name": "msToken", "value": "tok456"},
]


class TestListDouyinCookies:
    def test_empty_list(self, client):
        resp = client.get("/api/douyin-cookies")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_cookie_json_not_in_response(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        client.post(
            "/api/douyin-cookies",
            data={"name": "Main"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        resp = client.get("/api/douyin-cookies")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert "cookie_json_encrypted" not in rows[0]
        assert "id" in rows[0]
        assert "name" in rows[0]
        assert "created_at" in rows[0]


class TestCreateDouyinCookie:
    def test_create_valid(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Account 1"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Account 1"
        assert "id" in body
        assert "cookie_json_encrypted" not in body

    def test_create_missing_ttwid_returns_422(self, client):
        no_ttwid = [{"name": "msToken", "value": "tok"}]
        cookie_bytes = json.dumps(no_ttwid).encode()
        resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Bad"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert resp.status_code == 422

    def test_create_malformed_json_returns_422(self, client):
        resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Bad"},
            files={"file": ("cookies.json", b"not json", "application/json")},
        )
        assert resp.status_code == 422


class TestUpdateDouyinCookie:
    def test_update_cookie(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        create_resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Old"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert create_resp.status_code == 201
        cookie_id = create_resp.json()["id"]

        new_cookies = [{"name": "ttwid", "value": "newtoken999"}]
        new_bytes = json.dumps(new_cookies).encode()
        update_resp = client.put(
            f"/api/douyin-cookies/{cookie_id}",
            files={"file": ("cookies.json", new_bytes, "application/json")},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["id"] == cookie_id

    def test_update_nonexistent_returns_404(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        resp = client.put(
            "/api/douyin-cookies/9999",
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        assert resp.status_code == 404


class TestDeleteDouyinCookie:
    def test_delete_existing(self, client):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        create_resp = client.post(
            "/api/douyin-cookies",
            data={"name": "To delete"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        cookie_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/douyin-cookies/{cookie_id}")
        assert del_resp.status_code == 204

        list_resp = client.get("/api/douyin-cookies")
        assert list_resp.json() == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/douyin-cookies/9999")
        assert resp.status_code == 404

    def test_delete_in_use_by_pipeline_returns_409(self, client):  # noqa: keep together
        from datetime import datetime, timezone

        from api.models import ChannelPipeline

        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        create_resp = client.post(
            "/api/douyin-cookies",
            data={"name": "Linked"},
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )
        cookie_id = create_resp.json()["id"]

        now = datetime.now(timezone.utc)
        with Session(_api_db.engine) as s:
            s.add(ChannelPipeline(
                name="Douyin Billad", channel_url="https://www.douyin.com/user/x",
                fetch_count=5, source_type="douyin", douyin_cookie_id=cookie_id,
                interval_value=1, interval_unit="days",
                first_fire_at=now, next_fire_at=now, caption_template="{title}",
            ))
            s.commit()

        del_resp = client.delete(f"/api/douyin-cookies/{cookie_id}")
        assert del_resp.status_code == 409
        assert del_resp.json()["detail"]["code"] == "COOKIE_IN_USE"

        # Cookie still present after the rejected delete.
        assert len(client.get("/api/douyin-cookies").json()) == 1


class TestLoginCredentials:
    def _create(self, client, name="Main", login="", login_password=""):
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        data = {"name": name}
        if login:
            data["login"] = login
        if login_password:
            data["login_password"] = login_password
        return client.post(
            "/api/douyin-cookies",
            data=data,
            files={"file": ("cookies.json", cookie_bytes, "application/json")},
        )

    def test_create_with_login_and_password_saved(self, client):
        r = self._create(client, login="user@example.com", login_password="secret123")
        assert r.status_code == 201
        body = r.json()
        assert body["login"] == "user@example.com"
        assert body["login_password"] == "secret123"

    def test_login_password_encrypted_not_in_response(self, client):
        r = self._create(client, login="u@x.com", login_password="pw")
        assert "login_password_encrypted" not in r.json()
        assert "cookie_json_encrypted" not in r.json()

    def test_create_without_credentials_returns_null_fields(self, client):
        r = self._create(client)
        assert r.status_code == 201
        body = r.json()
        assert body["login"] is None
        assert body["login_password"] is None

    def test_list_decrypts_password(self, client):
        self._create(client, login="u@x.com", login_password="mypass")
        rows = client.get("/api/douyin-cookies").json()
        assert rows[0]["login_password"] == "mypass"
        assert "login_password_encrypted" not in rows[0]

    def test_put_updates_login_without_file(self, client):
        create_r = self._create(client, login="old@x.com", login_password="oldpass")
        cookie_id = create_r.json()["id"]

        r = client.put(
            f"/api/douyin-cookies/{cookie_id}",
            data={"login": "new@x.com", "login_password": "newpass"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["login"] == "new@x.com"
        assert body["login_password"] == "newpass"

    def test_put_updates_name_without_file(self, client):
        create_r = self._create(client, name="Old Name")
        cookie_id = create_r.json()["id"]

        r = client.put(
            f"/api/douyin-cookies/{cookie_id}",
            data={"name": "New Name"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"

    def test_put_without_file_preserves_cookie(self, client):
        """Updating only login fields must not wipe the stored cookie."""
        cookie_bytes = json.dumps(VALID_COOKIE_LIST).encode()
        create_r = client.post(
            "/api/douyin-cookies",
            data={"name": "X", "login": "a@b.com"},
            files={"file": ("c.json", cookie_bytes, "application/json")},
        )
        cookie_id = create_r.json()["id"]

        client.put(f"/api/douyin-cookies/{cookie_id}", data={"login": "c@d.com"})

        # Re-fetch and confirm the cookie is still decryptable by the scheduler
        from api.db import engine as _engine
        from api.models import DouyinCookie as _DC
        from api.services.secrets import decrypt
        from sqlmodel import Session, select
        with Session(_engine) as s:
            row = s.exec(select(_DC).where(_DC.id == cookie_id)).one()
            assert row.cookie_json_encrypted  # still present
            decrypted = decrypt(row.cookie_json_encrypted)
            assert "ttwid" in decrypted
