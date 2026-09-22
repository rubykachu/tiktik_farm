"""Tests for api/services/tiktok_channel.py and tiktok_validate.py.

yt-dlp is patched in api.services.ytdlp_source, which is where the calls
live; tiktok_channel adds the TikTok-only parts around them.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from api.services.tiktok_channel import DownloadError, VideoTooLong, download, list_top_n
from api.services.tiktok_validate import is_tiktok_url

PROFILE = "https://www.tiktok.com/@testcreator"
VIDEO = "https://www.tiktok.com/@testcreator/video/7123456789"

# 2026-09-01 and 2026-08-01 UTC.
TS_SEP = 1788307200
TS_AUG = 1785628800


def _ydl(entries=None, info=None):
    """A YoutubeDL context manager whose extract_info returns `entries` as a
    playlist, or `info` for a single video."""
    mock = MagicMock()
    instance = mock.return_value.__enter__.return_value
    instance.extract_info.return_value = (
        {"entries": entries} if entries is not None else info
    )
    return mock, instance


class TestIsTikTokUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.tiktok.com/@creator",
            "https://tiktok.com/@creator/video/123",
            "https://m.tiktok.com/@creator",
            "https://vm.tiktok.com/ZSabc/",
            "https://vt.tiktok.com/ZSabc/",
        ],
    )
    def test_accepts_tiktok_hosts(self, url):
        assert is_tiktok_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@creator",
            "https://www.douyin.com/user/abc",
            "https://tiktok.com.evil.test/@creator",
            "ftp://www.tiktok.com/@creator",
            "not a url",
            "",
        ],
    )
    def test_rejects_everything_else(self, url):
        assert not is_tiktok_url(url)


class TestListTopN:
    def test_rejects_non_tiktok_url(self):
        with pytest.raises(ValueError):
            list_top_n("https://www.youtube.com/@creator", 5)

    def test_maps_entries(self):
        entries = [
            {"id": "1", "title": "one", "url": VIDEO, "timestamp": TS_SEP},
            {"id": "2", "title": "two", "url": VIDEO, "timestamp": TS_SEP},
        ]
        mock, _ = _ydl(entries=entries)
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            out = list_top_n(PROFILE, 5)
        assert [v["id"] for v in out] == ["1", "2"]
        assert out[0]["title"] == "one"

    def test_limits_to_n(self):
        entries = [{"id": str(i), "title": "", "url": VIDEO} for i in range(10)]
        mock, _ = _ydl(entries=entries)
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            assert len(list_top_n(PROFILE, 3)) == 3

    def test_falls_back_to_a_built_url(self):
        mock, _ = _ydl(entries=[{"id": "7123", "title": ""}])
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            out = list_top_n(PROFILE, 5)
        assert out[0]["url"].endswith("/video/7123")

    def test_sync_from_date_filters_locally_not_via_dateafter(self):
        """TikTok flat entries carry `timestamp`, so the cutoff is applied
        here rather than relying on yt-dlp's `dateafter` for this extractor."""
        entries = [
            {"id": "new", "title": "", "url": VIDEO, "timestamp": TS_SEP},
            {"id": "old", "title": "", "url": VIDEO, "timestamp": TS_AUG},
        ]
        mock, instance = _ydl(entries=entries)
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            out = list_top_n(PROFILE, 5, sync_from_date=date(2026, 8, 15))
        assert [v["id"] for v in out] == ["new"]
        assert "dateafter" not in mock.call_args.args[0]

    def test_undated_entry_is_kept_rather_than_dropped(self):
        entries = [{"id": "nodate", "title": "", "url": VIDEO}]
        mock, _ = _ydl(entries=entries)
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            out = list_top_n(PROFILE, 5, sync_from_date=date(2026, 8, 15))
        assert [v["id"] for v in out] == ["nodate"]

    def test_empty_listing(self):
        mock, _ = _ydl(entries=[])
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            assert list_top_n(PROFILE, 5) == []


class TestDownload:
    def test_rejects_non_tiktok_url(self, tmp_path):
        with pytest.raises(ValueError):
            download("https://www.youtube.com/watch?v=abc", str(tmp_path))

    def test_returns_empty_transcript(self, tmp_path):
        """TikTok's extractor yields no subtitles, so the LLM rewrite works
        from title and description alone."""
        produced = str(tmp_path / "7123.mp4")
        mock, instance = _ydl(info={"id": "7123", "description": "a caption"})

        def add_pp(pp, when=None):
            pp.run({"filepath": produced})

        instance.add_post_processor.side_effect = add_pp
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            path, description, transcript = download(VIDEO, str(tmp_path))
        assert path == produced
        assert description == "a caption"
        assert transcript == ""

    def test_none_info_raises(self, tmp_path):
        mock, _ = _ydl(info=None)
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            with pytest.raises(DownloadError):
                download(VIDEO, str(tmp_path))

    def test_too_long_raises(self, tmp_path):
        import yt_dlp as real_yt_dlp

        mock, instance = _ydl(info={})
        instance.extract_info.side_effect = real_yt_dlp.utils.DownloadError(
            "Skipping abc: does not pass match_filter"
        )
        with patch("api.services.ytdlp_source.yt_dlp.YoutubeDL", mock):
            with pytest.raises(VideoTooLong):
                download(VIDEO, str(tmp_path), max_duration_seconds=60)


class TestImpersonation:
    """TikTok answers an ordinary Python TLS fingerprint with a stripped
    page and no error, so an un-impersonated listing comes back empty and
    reads like a channel that stopped posting. These pin that every TikTok
    request asks for a browser fingerprint."""

    def test_list_top_n_impersonates(self):
        with patch("api.services.tiktok_channel.list_entries", return_value=[]) as mocked:
            list_top_n(PROFILE, 5)
        assert mocked.call_args.kwargs["impersonate"] is True

    def test_download_impersonates(self, tmp_path):
        with patch(
            "api.services.tiktok_channel.download_video", return_value=("/tmp/x.mp4", {})
        ) as mocked:
            download(VIDEO, str(tmp_path))
        assert mocked.call_args.kwargs["impersonate"] is True

    def test_youtube_does_not_impersonate(self):
        """YouTube works on plain TLS, and impersonating costs a curl_cffi
        round trip it does not need."""
        from api.services import youtube_channel

        with patch("api.services.youtube_channel.list_entries", return_value=[]) as mocked:
            youtube_channel.list_top_n("https://www.youtube.com/@c/videos", 5)
        assert "impersonate" not in mocked.call_args.kwargs
