"""Tests for api/services/transcript.py — shared by youtube_channel and manual_download."""
from unittest.mock import patch, MagicMock

from api.services.transcript import fetch_transcript, vtt_to_text


class TestVttToText:
    def test_strips_cues_and_dedupes_rolling_captions(self):
        vtt = (
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: en\n\n"
            "1\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "<c>Hello</c> world\n\n"
            "2\n"
            "00:00:02.000 --> 00:00:04.000\n"
            "Hello world\n\n"  # rolling auto-caption repeat, must be deduped
            "3\n"
            "00:00:04.000 --> 00:00:06.000\n"
            "second line\n"
        )
        assert vtt_to_text(vtt) == "Hello world second line"


class TestFetchTranscript:
    def test_no_captions_returns_empty(self):
        assert fetch_transcript({}) == ""

    @patch("api.services.transcript.requests.get")
    def test_prefers_manual_subtitles_over_auto(self, mock_get):
        mock_get.return_value = MagicMock(
            text="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nmanual text\n",
            raise_for_status=MagicMock(),
        )
        info = {
            "subtitles": {"en": [{"ext": "vtt", "url": "https://x/manual.vtt"}]},
            "automatic_captions": {"en": [{"ext": "vtt", "url": "https://x/auto.vtt"}]},
        }
        assert fetch_transcript(info) == "manual text"
        mock_get.assert_called_once_with("https://x/manual.vtt", timeout=10)

    @patch("api.services.transcript.requests.get")
    def test_falls_back_to_automatic_captions(self, mock_get):
        mock_get.return_value = MagicMock(
            text="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nauto text\n",
            raise_for_status=MagicMock(),
        )
        info = {"automatic_captions": {"en": [{"ext": "vtt", "url": "https://x/auto.vtt"}]}}
        assert fetch_transcript(info) == "auto text"

    @patch("api.services.transcript.requests.get")
    def test_request_failure_returns_empty(self, mock_get):
        import requests as real_requests

        mock_get.side_effect = real_requests.RequestException("timeout")
        info = {"automatic_captions": {"en": [{"ext": "vtt", "url": "https://x/auto.vtt"}]}}
        assert fetch_transcript(info) == ""

    def test_tiktok_style_info_has_no_captions(self):
        """TikTok/Douyin info dicts have no subtitles/automatic_captions keys at all."""
        info = {"id": "123", "title": "t", "description": "d"}
        assert fetch_transcript(info) == ""
