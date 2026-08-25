"""Tests for POST /api/accounts/import-json.

Covers happy path, replace flag, size + count limits, sessionid required,
username path-traversal, file permission, and 409 when username exists.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from api.middleware.origin import LOCAL_ORIGIN


HEADERS_OK = {"Origin": LOCAL_ORIGIN}


def _valid_cookie_payload(extra: list[dict] | None = None) -> list[dict]:
    base = [
        {
            "name": "sessionid",
            "value": "abc123",
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "lax",
        },
        {
            "name": "tt_csrf_token",
            "value": "xyz",
            "domain": ".tiktok.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "lax",
        },
    ]
    return base + (extra or [])


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Spin up an isolated app with cookies + DB in tmp_path.

    Skip config.txt so Config.get() returns defaults (COOKIES_DIR=./CookiesDir);
    the singleton in tiktok_uploader.Config has a bug where reloading after a
    test session corrupts _options, so easier to just leave defaults active
    and rely on cwd=tmp_path for path isolation.
    """
    monkeypatch.chdir(tmp_path)
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    # Reset Config singleton in case a prior test set it.
    from tiktok_uploader.Config import Config
    Config._instance = None

    from api import db as _db
    from api.main import create_app

    test_engine = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(test_engine)
    _db.init_db()  # create tables on the overridden engine before any request.

    return TestClient(create_app())


class TestHappyPath:
    def test_201_creates_account_and_cookie_file(self, client, tmp_path):
        payload = _valid_cookie_payload()
        files = {"file": ("TK_cookies_foo.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "foo"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "foo"
        assert body["has_valid_session"] is True

        cookie_file = tmp_path / "CookiesDir" / "tiktok_session-foo.cookie"
        assert cookie_file.exists()
        # File perm should be 0600 (owner read/write only).
        mode = cookie_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_idempotent_with_replace_flag(self, client):
        payload = _valid_cookie_payload()
        files = {"file": ("TK_cookies_bar.json", json.dumps(payload), "application/json")}
        r1 = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "bar"},
            headers=HEADERS_OK,
        )
        assert r1.status_code == 201

        # Second import with replace=true should succeed.
        files2 = {"file": ("TK_cookies_bar.json", json.dumps(payload), "application/json")}
        r2 = client.post(
            "/api/accounts/import-json",
            files=files2,
            data={"username": "bar", "replace": "true"},
            headers=HEADERS_OK,
        )
        assert r2.status_code in (200, 201)


class TestRejections:
    def test_409_when_username_exists_without_replace(self, client):
        payload = _valid_cookie_payload()
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "dup"},
            headers=HEADERS_OK,
        )

        files2 = {"file": ("TK.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files2,
            data={"username": "dup"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "ACCOUNT_EXISTS"

    def test_400_missing_sessionid(self, client):
        payload = [
            {"name": "tt_csrf_token", "value": "x", "domain": ".tiktok.com"},
        ]
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "noses"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400
        assert "sessionid" in r.json()["detail"].lower()

    def test_400_oversize_file(self, client):
        # >256KB payload
        payload = _valid_cookie_payload(
            extra=[{"name": f"c{i}", "value": "x" * 2000, "domain": ".tiktok.com"} for i in range(200)]
        )
        body = json.dumps(payload)
        assert len(body) > 256 * 1024  # confirm fixture is over cap
        files = {"file": ("TK.json", body, "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "big"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400
        assert "size" in r.json()["detail"].lower() or "large" in r.json()["detail"].lower()

    def test_400_too_many_entries(self, client):
        payload = _valid_cookie_payload() + [
            {"name": f"c{i}", "value": "x", "domain": ".tiktok.com"} for i in range(250)
        ]
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "many"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400

    def test_400_malformed_json(self, client):
        files = {"file": ("TK.json", "{not valid json", "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "bad"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400

    def test_400_not_a_list(self, client):
        files = {"file": ("TK.json", json.dumps({"sessionid": "x"}), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "nolist"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400

    def test_400_username_path_traversal(self, client):
        payload = _valid_cookie_payload()
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "../etc/passwd"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400

    def test_400_username_with_slash(self, client):
        payload = _valid_cookie_payload()
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "foo/bar"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 400


class TestOriginEnforced:
    def test_403_without_origin(self, client):
        payload = _valid_cookie_payload()
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        r = client.post(
            "/api/accounts/import-json",
            files=files,
            data={"username": "foo"},
            # no Origin header
        )
        assert r.status_code == 403


class TestLoginCredentials:
    def _import(self, client, username="user1", login="", login_password=""):
        payload = _valid_cookie_payload()
        files = {"file": ("TK.json", json.dumps(payload), "application/json")}
        data = {"username": username}
        if login:
            data["login"] = login
        if login_password:
            data["login_password"] = login_password
        return client.post(
            "/api/accounts/import-json",
            files=files,
            data=data,
            headers=HEADERS_OK,
        )

    def test_import_with_login_and_password_saved(self, client):
        r = self._import(client, login="user@example.com", login_password="secret123")
        assert r.status_code == 201
        body = r.json()
        assert body["login"] == "user@example.com"
        assert body["login_password"] == "secret123"

    def test_login_password_encrypted_not_in_response(self, client):
        r = self._import(client, login="user@example.com", login_password="secret123")
        assert "login_password_encrypted" not in r.json()

    def test_import_without_credentials_returns_null_fields(self, client):
        r = self._import(client)
        assert r.status_code == 201
        body = r.json()
        assert body["login"] is None
        assert body["login_password"] is None

    def test_list_accounts_decrypts_password(self, client):
        self._import(client, login="u@x.com", login_password="mypass")
        r = client.get("/api/accounts", headers=HEADERS_OK)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["login_password"] == "mypass"
        assert "login_password_encrypted" not in rows[0]

    def test_patch_updates_login_and_password(self, client):
        create_r = self._import(client)
        account_id = create_r.json()["id"]

        r = client.patch(
            f"/api/accounts/{account_id}",
            json={"login": "new@example.com", "login_password": "newpass"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["login"] == "new@example.com"
        assert body["login_password"] == "newpass"
        assert "login_password_encrypted" not in body

    def test_patch_preserves_existing_password_when_not_provided(self, client):
        create_r = self._import(client, login="u@x.com", login_password="original")
        account_id = create_r.json()["id"]

        r = client.patch(
            f"/api/accounts/{account_id}",
            json={"display_name": "My Label"},
            headers=HEADERS_OK,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["login_password"] == "original"
        assert body["display_name"] == "My Label"
