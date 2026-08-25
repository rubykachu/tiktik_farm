"""Tests for /api/downloads — paste a URL, download, upload by hand."""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from api.middleware.origin import LOCAL_ORIGIN

HEADERS_OK = {"Origin": LOCAL_ORIGIN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("CookiesDir").mkdir()
    Path("VideosDirPath").mkdir()
    monkeypatch.setenv("MANUAL_DOWNLOAD_DIR", str(tmp_path / "manual_downloads"))
    from tiktok_uploader.Config import Config
    Config._instance = None
    from api import db as _db
    from api.db_migrations import apply_indexes, apply_schema_patches
    from api.main import create_app

    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    apply_schema_patches(eng)
    apply_indexes(eng)
    return TestClient(create_app())


def _fake_video(file_path, title="A title", description="A description"):
    from api.services.manual_download import DownloadedVideo

    return DownloadedVideo(
        file_path=file_path,
        title=title,
        description=description,
        duration_seconds=12,
        file_size=os.path.getsize(file_path),
    )


def _download_stub(content=b"video-bytes"):
    def _download(url, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "clip.mp4")
        with open(path, "wb") as fh:
            fh.write(content)
        return _fake_video(path)

    return _download


class TestCreate:
    def test_rejects_unsupported_host(self, client):
        r = client.post("/api/downloads", json={"urls": ["https://vimeo.com/1"]},
                        headers=HEADERS_OK)
        assert r.status_code == 422
        assert "vimeo.com" in r.json()["detail"]

    def test_rejects_empty_list(self, client):
        r = client.post("/api/downloads", json={"urls": []}, headers=HEADERS_OK)
        assert r.status_code == 422

    def test_accepts_multiple_urls(self, client):
        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            r = client.post(
                "/api/downloads",
                json={"urls": [
                    "https://www.youtube.com/watch?v=a",
                    "https://www.tiktok.com/@u/video/1",
                ]},
                headers=HEADERS_OK,
            )
        assert r.status_code == 201
        assert [row["source_type"] for row in r.json()] == ["youtube", "tiktok"]

    def test_ready_after_download(self, client):
        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row = client.get("/api/downloads").json()[0]
        assert row["status"] == "ready"
        assert row["title"] == "A title"
        assert row["file_size"] == len(b"video-bytes")

    def test_failure_recorded_on_row(self, client):
        from api.services.manual_download import DownloadFailed

        with patch("api.services.manual_download.download",
                   side_effect=DownloadFailed("video unavailable")):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row = client.get("/api/downloads").json()[0]
        assert row["status"] == "failed"
        assert "video unavailable" in row["error_text"]


class TestFile:
    def test_serves_ready_file(self, client):
        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row_id = client.get("/api/downloads").json()[0]["id"]

        r = client.get(f"/api/downloads/{row_id}/file")
        assert r.status_code == 200
        assert r.content == b"video-bytes"

    def test_conflict_while_not_ready(self, client):
        from api.services.manual_download import DownloadFailed

        with patch("api.services.manual_download.download",
                   side_effect=DownloadFailed("nope")):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row_id = client.get("/api/downloads").json()[0]["id"]

        assert client.get(f"/api/downloads/{row_id}/file").status_code == 409

    def test_404_for_unknown_id(self, client):
        assert client.get("/api/downloads/9999/file").status_code == 404


class TestSignedFile:
    """The Telegram link opens without Basic Auth, so the HMAC is the gate."""

    def _ready_id(self, client):
        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        return client.get("/api/downloads").json()[0]["id"]

    def test_valid_signature_serves_file(self, client):
        from api.services.manual_download import sign_id, signed_path

        row_id = self._ready_id(client)
        r = client.get(signed_path(row_id, sign_id(row_id)))
        assert r.status_code == 200
        assert r.content == b"video-bytes"

    def test_wrong_signature_is_404(self, client):
        from api.services.manual_download import signed_path

        row_id = self._ready_id(client)
        assert client.get(signed_path(row_id, "0" * 64)).status_code == 404

    def test_signature_is_not_reusable_across_ids(self, client):
        from api.services.manual_download import sign_id, signed_path

        row_id = self._ready_id(client)
        assert client.get(signed_path(row_id + 1, sign_id(row_id))).status_code == 404

    def test_unknown_id_with_valid_signature_is_404(self, client):
        from api.services.manual_download import sign_id, signed_path

        missing = 4242
        assert client.get(signed_path(missing, sign_id(missing))).status_code == 404


class TestCaption:
    def _ready_row(self, client):
        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        return client.get("/api/downloads").json()[0]["id"]

    def test_stores_generated_caption(self, client):
        row_id = self._ready_row(client)
        with patch("api.routers.downloads.rewrite_with_llm", return_value="A snappy caption"):
            r = client.post(f"/api/downloads/{row_id}/caption", json={}, headers=HEADERS_OK)

        assert r.status_code == 200
        assert r.json()["ai_caption"] == "A snappy caption"
        assert client.get("/api/downloads").json()[0]["ai_caption"] == "A snappy caption"

    def test_uses_sample_prompt_by_default(self, client):
        from api.services.caption_prompt import SAMPLE_CAPTION_PROMPT

        row_id = self._ready_row(client)
        with patch("api.routers.downloads.rewrite_with_llm", return_value="X") as mock_rw:
            client.post(f"/api/downloads/{row_id}/caption", json={}, headers=HEADERS_OK)

        assert mock_rw.call_args[0][0] == SAMPLE_CAPTION_PROMPT.strip()

    def test_422_when_llm_returns_title_unchanged(self, client):
        row_id = self._ready_row(client)
        with patch("api.routers.downloads.rewrite_with_llm", return_value="A title"):
            r = client.post(f"/api/downloads/{row_id}/caption", json={}, headers=HEADERS_OK)

        assert r.status_code == 422


class TestRetention:
    def test_delete_removes_row_and_file(self, client, tmp_path):
        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row_id = client.get("/api/downloads").json()[0]["id"]
        row_dir = tmp_path / "manual_downloads" / str(row_id)
        assert row_dir.exists()

        assert client.delete(f"/api/downloads/{row_id}", headers=HEADERS_OK).status_code == 204
        assert not row_dir.exists()
        assert client.get("/api/downloads").json() == []

    def test_purge_removes_expired(self, client, tmp_path):
        from api import db as _db
        from api.db import now_utc
        from api.models import ManualDownload
        from api.services.manual_download import purge_expired

        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row_id = client.get("/api/downloads").json()[0]["id"]
        with Session(_db.engine) as s:
            row = s.get(ManualDownload, row_id)
            row.expires_at = now_utc() - timedelta(minutes=1)
            s.add(row)
            s.commit()

        assert purge_expired() == 1
        assert not (tmp_path / "manual_downloads" / str(row_id)).exists()
        assert client.get("/api/downloads").json() == []

    def test_listing_purges_expired_without_the_scheduler(self, client, tmp_path):
        """Retention must hold even when only the API is running."""
        from api import db as _db
        from api.db import now_utc
        from api.models import ManualDownload

        with patch("api.services.manual_download.download", side_effect=_download_stub()):
            client.post("/api/downloads",
                        json={"urls": ["https://www.youtube.com/watch?v=a"]},
                        headers=HEADERS_OK)
        row_id = client.get("/api/downloads").json()[0]["id"]
        with Session(_db.engine) as s:
            row = s.get(ManualDownload, row_id)
            row.expires_at = now_utc() - timedelta(minutes=1)
            s.add(row)
            s.commit()

        assert client.get("/api/downloads").json() == []
        assert not (tmp_path / "manual_downloads" / str(row_id)).exists()

    def test_rejects_more_than_five_urls(self, client):
        r = client.post(
            "/api/downloads",
            json={"urls": [f"https://www.youtube.com/watch?v={i}" for i in range(6)]},
            headers=HEADERS_OK,
        )
        assert r.status_code == 422


class TestSourceDetection:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.youtube.com/watch?v=a", "youtube"),
        ("https://youtu.be/a", "youtube"),
        ("https://www.tiktok.com/@u/video/1", "tiktok"),
        ("https://vt.tiktok.com/ZS1/", "tiktok"),
        ("https://www.douyin.com/video/1", "douyin"),
        ("https://v.douyin.com/abc/", "douyin"),
    ])
    def test_detects_supported_hosts(self, url, expected):
        from api.services.manual_download import detect_source

        assert detect_source(url) == expected

    @pytest.mark.parametrize("url", [
        "https://vimeo.com/1",
        "not-a-url",
        "https://youtube.com.evil.test/watch?v=a",
    ])
    def test_rejects_others(self, url):
        from api.services.manual_download import UnsupportedUrl, detect_source

        with pytest.raises(UnsupportedUrl):
            detect_source(url)
