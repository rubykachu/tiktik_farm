"""Tests for api/services/ytdlp_opts.py — the YouTube player_client escape hatch."""
from api.services.ytdlp_opts import extractor_args


class TestExtractorArgs:
    def test_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("YTDLP_PLAYER_CLIENTS", raising=False)
        assert extractor_args() == {}

    def test_blank_value_is_ignored(self, monkeypatch):
        monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", "  , ,")
        assert extractor_args() == {}

    def test_parses_client_list(self, monkeypatch):
        monkeypatch.setenv("YTDLP_PLAYER_CLIENTS", "tv, web_safari ")
        assert extractor_args() == {
            "extractor_args": {"youtube": {"player_client": ["tv", "web_safari"]}}
        }
