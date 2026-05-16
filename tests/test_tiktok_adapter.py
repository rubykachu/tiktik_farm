"""Unit tests for TikTok publish response parsing."""
from __future__ import annotations

from unittest.mock import patch

from api.services.tiktok_adapter import perform_upload


@patch("api.services.tiktok_adapter._tiktok.upload_video")
def test_perform_upload_share_url(mock_upload):
    mock_upload.return_value = {
        "status_code": 0,
        "share_url": "https://www.tiktok.com/@someuser/video/1234567890123456789",
    }
    ok, vid, url = perform_upload("session_user", "/tmp/v.mp4", "caption")
    assert ok is True
    assert vid == "1234567890123456789"
    assert url == "https://www.tiktok.com/@someuser/video/1234567890123456789"


@patch("api.services.tiktok_adapter._tiktok.upload_video")
def test_perform_upload_nested_aweme_id(mock_upload):
    mock_upload.return_value = {
        "status_code": 0,
        "data": {"aweme": {"aweme_id": "998877"}},
    }
    ok, vid, url = perform_upload("acct", "/tmp/v.mp4", "caption")
    assert ok is True
    assert vid == "998877"
    assert url == "https://www.tiktok.com/@acct/video/998877"


@patch("api.services.tiktok_adapter._tiktok.upload_video")
def test_perform_upload_true_legacy_no_ids(mock_upload):
    mock_upload.return_value = True
    ok, vid, url = perform_upload("acct", "/tmp/v.mp4", "caption")
    assert ok is True
    assert vid is None
    assert url is None

