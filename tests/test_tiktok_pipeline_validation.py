"""API validation for pipelines whose source is TikTok.

Pins the three rules that differ from the other sources: the channel URL has
to be a TikTok one, a TikTok pipeline needs no cookie and must not be given
a Douyin one, and the URL check keeps firing when the URL is edited later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.middleware.origin import LOCAL_ORIGIN

HEADERS_OK = {"Origin": LOCAL_ORIGIN}

TIKTOK_PROFILE = "https://www.tiktok.com/@creator"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "test.key"))
    from tiktok_uploader.Config import Config

    Config._instance = None
    from api import db as _db
    from api.db_migrations import apply_indexes, apply_schema_patches
    from api.main import create_app
    from api.services import secrets as _sec

    _sec._reset_for_tests()
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    apply_schema_patches(eng)
    apply_indexes(eng)
    yield TestClient(create_app(), headers=HEADERS_OK)
    _sec._reset_for_tests()


def _make_account() -> int:
    from sqlmodel import Session

    from api import db as _db
    from api.models import Account

    with Session(_db.engine) as s:
        a = Account(username="user1", cookie_path="/x/user1.cookie")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _payload(account_id: int, **overrides):
    base = {
        "name": "TikTok Pipeline",
        "source_type": "tiktok",
        "channel_url": TIKTOK_PROFILE,
        "fetch_count": 3,
        "interval_value": 1,
        "interval_unit": "hours",
        "first_fire_at": (_now() + timedelta(hours=1)).isoformat(),
        "caption_template": "{title}",
        "account_ids": [account_id],
    }
    base.update(overrides)
    return base


class TestCreate:
    def test_tiktok_pipeline_is_created_without_any_cookie(self, client):
        """Unlike Douyin, a TikTok creator feed is readable without login,
        so the form must not demand a cookie."""
        resp = client.post("/api/pipelines", json=_payload(_make_account()))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["source_type"] == "tiktok"
        assert body["douyin_cookie_id"] is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@creator/videos",
            "https://www.douyin.com/user/MS4wLjAB",
            "https://tiktok.com.evil.test/@creator",
        ],
    )
    def test_non_tiktok_url_is_rejected(self, client, url):
        resp = client.post("/api/pipelines", json=_payload(_make_account(), channel_url=url))
        assert resp.status_code == 422
        assert "TikTok" in resp.text

    def test_douyin_cookie_on_a_tiktok_pipeline_is_rejected(self, client):
        resp = client.post(
            "/api/pipelines", json=_payload(_make_account(), douyin_cookie_id=1)
        )
        assert resp.status_code == 422
        assert "douyin_cookie_id" in resp.text

    def test_unknown_source_type_is_rejected(self, client):
        resp = client.post(
            "/api/pipelines", json=_payload(_make_account(), source_type="vimeo")
        )
        assert resp.status_code == 422


class TestUpdate:
    def _create(self, client) -> int:
        resp = client.post("/api/pipelines", json=_payload(_make_account()))
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def test_url_can_be_changed_to_another_tiktok_url(self, client):
        pipeline_id = self._create(client)
        resp = client.patch(
            f"/api/pipelines/{pipeline_id}",
            json={"channel_url": "https://www.tiktok.com/@another"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["channel_url"] == "https://www.tiktok.com/@another"

    def test_url_cannot_be_changed_to_a_youtube_url(self, client):
        pipeline_id = self._create(client)
        resp = client.patch(
            f"/api/pipelines/{pipeline_id}",
            json={"channel_url": "https://www.youtube.com/@creator/videos"},
        )
        assert resp.status_code == 422
        assert "TikTok" in resp.text
