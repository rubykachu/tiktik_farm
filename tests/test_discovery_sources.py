"""Tests for the TikTok and Douyin discovery sources, and for the contract
every source in api.services.discovery_sources has to satisfy.

The subprocess bridges (tiktok_search.js, douyin_bridge.py) are mocked at
_run_bridge, so nothing here starts a browser, touches the network, or
needs a cookie. tests/test_channel_discovery.py covers YouTube.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from api import db as _db
from api.db_migrations import seed_app_settings
from api.services import discovery_settings as settings
from api.services import discovery_sources
from api.services.discovery_sources import base as ds_base
from api.services.discovery_sources import douyin, tiktok


@pytest.fixture
def engine(tmp_path):
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    seed_app_settings(eng)
    return eng


def _days_ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------- the interface every source implements -----------------------------


class TestSourceRegistry:
    def test_every_platform_exposes_the_full_interface(self):
        for name in discovery_sources.PLATFORMS:
            source = discovery_sources.get(name)
            for attr in (
                "SINGLE_FEED",
                "MARKET_AWARE",
                "SEARCH_ENABLED",
                "SUPPORTS_SUGGESTIONS",
                "PIPELINE_SOURCE_TYPE",
                "suggest_keywords",
                "search_channels",
                "fetch_channel",
            ):
                assert hasattr(source, attr), f"{name} is missing {attr}"

    def test_unknown_platform_raises(self):
        with pytest.raises(ValueError):
            discovery_sources.get("vimeo")

    def test_pipeline_source_types_exist_on_channel_pipeline(self):
        """A source claiming a pipeline source_type must name one the
        pipeline schema actually accepts, or the add button would produce a
        422 the user cannot act on."""
        from api.schemas import PipelineCreate

        allowed = PipelineCreate.model_fields["source_type"].annotation.__args__
        for name in discovery_sources.PLATFORMS:
            source_type = discovery_sources.get(name).PIPELINE_SOURCE_TYPE
            assert source_type is None or source_type in allowed


# ---------- single-feed cadence -----------------------------------------------


class TestFeedStats:
    def test_no_posts_is_infinitely_silent(self):
        days, cadence, newest = ds_base.feed_stats([])
        assert days == float("inf") and cadence is None and newest is None

    def test_one_post_has_recency_but_no_cadence(self):
        days, cadence, newest = ds_base.feed_stats([_days_ago(3)])
        assert 2.9 < days < 3.1
        assert cadence is None
        assert newest is not None

    def test_cadence_is_span_over_gaps(self):
        posts = [_days_ago(1), _days_ago(5), _days_ago(11)]
        days, cadence, _ = ds_base.feed_stats(posts)
        assert 0.9 < days < 1.1
        assert 4.9 < cadence < 5.1  # 10 days across 2 gaps


class TestScore:
    def test_single_feed_cadence_takes_the_whole_cadence_budget(self, engine):
        settings.set_setting("discovery.target_days_per_video", "1")
        data = ds_base.ChannelData(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=0,
            cadence_short_days_per_video=1.0,
            cadence_long_days_per_video=None,
            latest_video_posted_at=None,
            channel_description="",
            median_duration_seconds=None,
        )
        # 20 recency + 65 cadence; the same channel on YouTube would get 45.
        assert ds_base.score(data, single_feed=True) == 85.0
        assert ds_base.score(data, single_feed=False) == 65.0


# ---------- TikTok -------------------------------------------------------------


SEARCH_PAYLOAD = {
    "results": {
        "dance": [
            {
                "channel_id": "123",
                "unique_id": "dancer",
                "nickname": "Dancer",
                "follower_count": 5000,
                "signature": "I dance",
                "sec_uid": "MS4wSEC",
            }
        ]
    },
    "errors": {},
}


class TestTikTokSearch:
    def test_maps_creators_to_profile_urls(self):
        with patch.object(tiktok, "_run_bridge", return_value=SEARCH_PAYLOAD):
            result = tiktok.search_channels(["dance"], "US", "en")
        ref = result["dance"][0]
        assert ref.channel_id == "123"
        assert ref.channel_url == "https://www.tiktok.com/@dancer"
        assert ref.channel_title == "Dancer"
        assert ref.subscriber_count == 5000
        assert ref.feed_key == "MS4wSEC"

    def test_keyword_with_no_results_is_still_in_the_map(self):
        with patch.object(tiktok, "_run_bridge", return_value=SEARCH_PAYLOAD):
            result = tiktok.search_channels(["dance", "nothing"], "US", "en")
        assert result["nothing"] == []

    def test_one_failed_keyword_does_not_lose_the_others(self):
        payload = {**SEARCH_PAYLOAD, "errors": {"nothing": "blocked"}}
        with patch.object(tiktok, "_run_bridge", return_value=payload):
            result = tiktok.search_channels(["dance", "nothing"], "US", "en")
        assert len(result["dance"]) == 1

    def test_raises_when_every_keyword_failed(self):
        payload = {"results": {"a": []}, "errors": {"a": "blocked"}}
        with patch.object(tiktok, "_run_bridge", return_value=payload):
            with pytest.raises(tiktok.TikTokSearchError):
                tiktok.search_channels(["a"], "US", "en")

    def test_suggestions_degrade_to_empty_on_failure(self):
        with patch.object(tiktok, "_run_bridge", side_effect=tiktok.TikTokSearchError("nope")):
            assert tiktok.suggest_keywords("dance", "US", "en") == []


class TestTikTokFetchChannel:
    def _ref(self, followers=5000, feed_key="MS4wSEC"):
        return ds_base.ChannelRef(
            channel_id="123",
            channel_url="https://www.tiktok.com/@dancer",
            channel_title="Dancer",
            subscriber_count=followers,
            channel_description="I dance",
            feed_key=feed_key,
        )

    def test_rejects_below_min_subscribers_without_a_profile_request(self, engine):
        settings.set_setting("discovery.min_subscribers", "1000")
        with patch.object(tiktok, "_flat_extract") as mocked:
            assert tiktok.fetch_channel(self._ref(followers=50)) is None
        mocked.assert_not_called()

    def test_feed_is_listed_by_sec_uid_not_by_profile_url(self, engine):
        """The profile URL makes yt-dlp scrape the profile page first to
        recover the secUid, and TikTok does not always serve one — the
        search already handed us that id, so use it."""
        settings.set_setting("discovery.min_subscribers", "0")
        entries = [{"timestamp": int(_days_ago(1).timestamp()), "view_count": 1, "duration": 5}]
        with patch.object(tiktok, "_flat_extract", return_value={"entries": entries}) as mocked:
            tiktok.fetch_channel(self._ref())
        assert mocked.call_args_list[0].args[0] == "tiktokuser:MS4wSEC"

    def test_falls_back_to_the_profile_url_when_sec_uid_listing_is_empty(self, engine):
        settings.set_setting("discovery.min_subscribers", "0")
        entries = [{"timestamp": int(_days_ago(1).timestamp()), "view_count": 1, "duration": 5}]

        def fake(url):
            return {"entries": entries} if url == "https://www.tiktok.com/@dancer" else {"entries": []}

        with patch.object(tiktok, "_flat_extract", side_effect=fake) as mocked:
            data = tiktok.fetch_channel(self._ref())
        assert [c.args[0] for c in mocked.call_args_list] == [
            "tiktokuser:MS4wSEC",
            "https://www.tiktok.com/@dancer",
        ]
        assert data is not None

    def test_candidate_without_sec_uid_uses_the_profile_url(self, engine):
        settings.set_setting("discovery.min_subscribers", "0")
        entries = [{"timestamp": int(_days_ago(1).timestamp()), "view_count": 1, "duration": 5}]
        with patch.object(tiktok, "_flat_extract", return_value={"entries": entries}) as mocked:
            tiktok.fetch_channel(self._ref(feed_key=None))
        assert [c.args[0] for c in mocked.call_args_list] == ["https://www.tiktok.com/@dancer"]

    def test_metrics_come_from_the_one_flat_listing(self, engine):
        settings.set_setting("discovery.min_subscribers", "0")
        entries = [
            {"timestamp": int(_days_ago(1).timestamp()), "view_count": 300, "duration": 20},
            {"timestamp": int(_days_ago(3).timestamp()), "view_count": 100, "duration": 10},
            {"timestamp": int(_days_ago(5).timestamp()), "view_count": 200, "duration": 30},
        ]
        with patch.object(tiktok, "_flat_extract", return_value={"entries": entries}) as mocked:
            data = tiktok.fetch_channel(self._ref())
        assert mocked.call_count == 1  # the secUid listing answered; no fallback spent
        assert data.median_view_count == 200
        assert data.median_duration_seconds == 20
        assert 0.9 < data.days_since_last_video < 1.1
        assert 1.9 < data.cadence_short_days_per_video < 2.1  # 4 days over 2 gaps
        assert data.cadence_long_days_per_video is None
        assert data.channel_description == "I dance"

    def test_profile_with_no_posts_is_rejected(self, engine):
        settings.set_setting("discovery.min_subscribers", "0")
        with patch.object(tiktok, "_flat_extract", return_value={"entries": []}), patch.object(
            tiktok.time, "sleep"
        ):
            assert tiktok.fetch_channel(self._ref()) is None

    def test_an_empty_listing_is_retried_before_the_candidate_is_dropped(self, engine):
        """TikTok answers a throttled request with an empty listing, not an
        error, so one empty answer must not be read as a dead account."""
        settings.set_setting("discovery.min_subscribers", "0")
        entries = [{"timestamp": int(_days_ago(1).timestamp()), "view_count": 1, "duration": 5}]
        answers = [{"entries": []}, {"entries": []}, {"entries": entries}]
        with patch.object(tiktok, "_flat_extract", side_effect=answers), patch.object(
            tiktok.time, "sleep"
        ) as slept:
            data = tiktok.fetch_channel(self._ref(feed_key=None))
        assert data is not None
        assert slept.call_count == 2

    def test_retries_give_up_after_the_attempt_budget(self, engine):
        settings.set_setting("discovery.min_subscribers", "0")
        with patch.object(tiktok, "_flat_extract", return_value={"entries": []}) as mocked, patch.object(
            tiktok.time, "sleep"
        ):
            assert tiktok.fetch_channel(self._ref(feed_key=None)) is None
        assert mocked.call_count == tiktok._FEED_ATTEMPTS


# ---------- Douyin -------------------------------------------------------------


class TestDouyinSearch:
    def test_maps_creators_to_user_urls(self):
        payload = {
            "results": {
                "舞蹈": [
                    {
                        "channel_id": "SEC-1",
                        "channel_url": "https://www.douyin.com/user/SEC-1",
                        "nickname": "跳舞",
                        "follower_count": 900,
                        "signature": "bio",
                    }
                ]
            }
        }
        with patch.object(douyin, "_run_bridge", return_value=payload):
            refs = douyin.search_channels(["舞蹈"], "CN", "zh-CN")["舞蹈"]
        assert refs[0].channel_id == "SEC-1"
        assert refs[0].channel_url == "https://www.douyin.com/user/SEC-1"

    def test_refused_keyword_fails_the_scan_with_douyins_own_words(self):
        """An empty result and a refusal need different fixes — a refusal
        must not read as "this keyword found nothing"."""
        payload = {"results": {"舞蹈": []}, "errors": {"舞蹈": "Douyin status_code=2483: 请先登录，再继续搜索吧"}}
        with patch.object(douyin, "_run_bridge", return_value=payload):
            with pytest.raises(douyin.DouyinSearchError) as exc:
                douyin.search_channels(["舞蹈"], "CN", "zh-CN")
        assert "2483" in str(exc.value)
        assert "请先登录" in str(exc.value)

    def test_one_refused_keyword_does_not_lose_the_others(self):
        payload = {
            "results": {
                "舞蹈": [
                    {
                        "channel_id": "SEC-1",
                        "channel_url": "https://www.douyin.com/user/SEC-1",
                        "nickname": "跳舞",
                        "follower_count": 900,
                        "signature": "",
                    }
                ],
                "美食": [],
            },
            "errors": {"美食": "Douyin status_code=2483: 请先登录，再继续搜索吧"},
        }
        with patch.object(douyin, "_run_bridge", return_value=payload):
            out = douyin.search_channels(["舞蹈", "美食"], "CN", "zh-CN")
        assert len(out["舞蹈"]) == 1
        assert out["美食"] == []

    def test_missing_cookie_fails_the_scan_with_a_readable_message(self, engine):
        with pytest.raises(douyin.DouyinSearchError) as exc:
            douyin._cookies()
        assert "cookie" in str(exc.value).lower()


class TestDouyinFetchChannel:
    def _ref(self):
        return ds_base.ChannelRef(
            channel_id="SEC-1",
            channel_url="https://www.douyin.com/user/SEC-1",
            channel_title="跳舞",
            subscriber_count=0,
            channel_description="",
        )

    def test_profile_follower_count_wins_over_the_search_one(self, engine):
        settings.set_setting("discovery.min_subscribers", "500")
        payload = {
            "follower_count": 900,
            "signature": "bio",
            "posts": [
                {"create_time": int(_days_ago(1).timestamp()), "play_count": 100, "duration_seconds": 15},
                {"create_time": int(_days_ago(3).timestamp()), "play_count": 300, "duration_seconds": 25},
            ],
        }
        with patch.object(douyin, "_run_bridge", return_value=payload):
            data = douyin.fetch_channel(self._ref())
        assert data.subscriber_count == 900
        assert data.cadence_long_days_per_video is None
        assert 1.9 < data.cadence_short_days_per_video < 2.1

    def test_rejects_below_min_subscribers(self, engine):
        settings.set_setting("discovery.min_subscribers", "5000")
        payload = {"follower_count": 900, "signature": "", "posts": []}
        with patch.object(douyin, "_run_bridge", return_value=payload):
            assert douyin.fetch_channel(self._ref()) is None

    def test_bridge_failure_drops_the_candidate_instead_of_the_scan(self, engine):
        settings.set_setting("discovery.min_subscribers", "0")
        with patch.object(douyin, "_run_bridge", side_effect=douyin.DouyinSearchError("expired")):
            assert douyin.fetch_channel(self._ref()) is None
