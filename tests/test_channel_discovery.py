"""Tests for api.services.channel_discovery.

yt-dlp/HTTP calls are mocked at the module's own wrapper functions
(_flat_extract, _full_extract, _http_get_text) — these tests never touch
the network. DB-backed tests use the same override_engine fixture pattern
as tests/test_discovery_settings.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from api import db as _db
from api.db_migrations import seed_app_settings
from api.models import ChannelPipeline, DiscoveryCandidate, DiscoveryScanResult, DiscoveryScanRun
from api.services import channel_discovery as cd
from api.services import discovery_settings as settings


@pytest.fixture
def engine(tmp_path):
    eng = _db.make_engine(f"sqlite:///{tmp_path}/test.db")
    _db.override_engine(eng)
    _db.init_db()
    seed_app_settings(eng)
    return eng


# ---------- normalize_keyword_set --------------------------------------------


class TestNormalizeKeywordSet:
    def test_order_independent(self):
        assert cd.normalize_keyword_set(["b", "a"]) == cd.normalize_keyword_set(["a", "b"])

    def test_case_and_whitespace_insensitive(self):
        assert cd.normalize_keyword_set([" Funny Drawings "]) == cd.normalize_keyword_set(["funny drawings"])

    def test_dedupes(self):
        assert cd.normalize_keyword_set(["a", "a", "b"]) == cd.normalize_keyword_set(["a", "b"])

    def test_blank_entries_ignored(self):
        assert cd.normalize_keyword_set(["a", "  ", ""]) == cd.normalize_keyword_set(["a"])


# ---------- normalize_channel_key --------------------------------------------


class TestNormalizeChannelKey:
    def test_strips_videos_suffix(self):
        assert cd.normalize_channel_key("https://www.youtube.com/@BoBoKids/videos") == "@bobokids"

    def test_strips_shorts_suffix(self):
        assert cd.normalize_channel_key("https://www.youtube.com/@BoBoKids/shorts") == "@bobokids"

    def test_bare_channel_url(self):
        assert cd.normalize_channel_key("https://www.youtube.com/channel/UCabc123") == "ucabc123"

    def test_trailing_slash(self):
        assert cd.normalize_channel_key("https://www.youtube.com/@BoBoKids/") == "@bobokids"

    def test_different_representations_do_not_match(self):
        by_handle = cd.normalize_channel_key("https://www.youtube.com/@BoBoKids")
        by_id = cd.normalize_channel_key("https://www.youtube.com/channel/UCabc123")
        assert by_handle != by_id


# ---------- score_candidate ---------------------------------------------------


class TestScoreCandidate:
    def test_fresh_frequent_popular_with_shorts_scores_near_max(self, engine):
        settings.set_setting("discovery.target_days_per_video", "1")
        m = cd.ChannelMetrics(
            subscriber_count=5000,
            median_view_count=1_000_000,
            days_since_last_video=0.5,
            cadence_videos_days_per_video=1.0,
            cadence_shorts_days_per_video=0.5,
        )
        assert cd.score_candidate(m) >= 95

    def test_stale_channel_scores_low_on_recency(self, engine):
        settings.set_setting("discovery.silent_months", "6")
        m = cd.ChannelMetrics(
            subscriber_count=5000,
            median_view_count=1000,
            days_since_last_video=179,  # just under the 180-day cutoff
            cadence_videos_days_per_video=None,
            cadence_shorts_days_per_video=None,
        )
        score = cd.score_candidate(m)
        assert score < 10

    def test_no_cadence_contributes_zero(self, engine):
        m = cd.ChannelMetrics(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=0,
            cadence_videos_days_per_video=None,
            cadence_shorts_days_per_video=None,
        )
        assert cd.score_candidate(m) == 20.0

    def test_shorts_cadence_at_target_gets_full_45(self, engine):
        settings.set_setting("discovery.target_days_per_video", "1")
        m = cd.ChannelMetrics(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=999999,
            cadence_videos_days_per_video=None,
            cadence_shorts_days_per_video=1.0,
        )
        assert cd.score_candidate(m) == 45.0

    def test_shorts_cadence_twice_target_gets_half(self, engine):
        settings.set_setting("discovery.target_days_per_video", "1")
        m = cd.ChannelMetrics(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=999999,
            cadence_videos_days_per_video=None,
            cadence_shorts_days_per_video=2.0,
        )
        assert cd.score_candidate(m) == 22.5

    def test_video_cadence_at_target_gets_full_20(self, engine):
        settings.set_setting("discovery.target_days_per_video", "1")
        m = cd.ChannelMetrics(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=999999,
            cadence_videos_days_per_video=1.0,
            cadence_shorts_days_per_video=None,
        )
        assert cd.score_candidate(m) == 20.0

    def test_no_shorts_cadence_loses_the_45_bucket_no_fallback(self, engine):
        """A channel with only long-form videos does not get the video
        cadence substituted into the Shorts bucket — Shorts is the primary
        signal this tool is meant to prioritize, so a pure long-form
        channel is capped well below one with an active Shorts cadence."""
        settings.set_setting("discovery.target_days_per_video", "1")
        m = cd.ChannelMetrics(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=0,
            cadence_videos_days_per_video=1.0,
            cadence_shorts_days_per_video=None,
        )
        assert cd.score_candidate(m) == 40.0  # 20 recency + 20 video cadence, 0 shorts

    def test_view_count_only_adds_never_subtracts(self, engine):
        """Low view/sub must not be penalized — a viral channel carries
        more copyright-strike risk and should not be favored for it."""
        settings.set_setting("discovery.target_days_per_video", "1")
        low_view = cd.ChannelMetrics(
            subscriber_count=100,
            median_view_count=0,
            days_since_last_video=0,
            cadence_videos_days_per_video=None,
            cadence_shorts_days_per_video=1.0,
        )
        assert cd.score_candidate(low_view) == 65.0  # 20 recency + 45 shorts cadence, 0 view


# ---------- fetch_phase1 -------------------------------------------------------


class TestFetchPhase1:
    def test_rejects_below_min_subscribers_without_extra_shorts_call(self, engine):
        settings.set_setting("discovery.min_subscribers", "1000")
        videos_info = {
            "channel_follower_count": 50,
            "entries": [{"view_count": 10} for _ in range(5)],
        }
        with patch.object(cd, "_flat_extract", return_value=videos_info) as mocked:
            result = cd.fetch_phase1("https://www.youtube.com/@tiny")
        assert result is None
        assert mocked.call_count == 1

    def test_survivor_fetches_shorts_and_computes_median(self, engine):
        settings.set_setting("discovery.min_subscribers", "100")
        videos_info = {
            "channel_follower_count": 5000,
            "entries": [{"view_count": v} for v in [10, 20, 30, 40, 50]],
        }
        shorts_info = {
            "channel_follower_count": 5000,
            "entries": [{"view_count": 5}, {"view_count": 15}],
        }

        def fake_flat(url, playlistend=None, lang=None):
            return shorts_info if url.endswith("/shorts") else videos_info

        with patch.object(cd, "_flat_extract", side_effect=fake_flat):
            result = cd.fetch_phase1("https://www.youtube.com/@channel")

        assert result is not None
        assert result.subscriber_count == 5000
        assert result.median_view_count == 30
        assert len(result.shorts_entries) == 2

    def test_falls_back_to_shorts_when_no_videos_tab(self, engine):
        settings.set_setting("discovery.min_subscribers", "100")
        shorts_info = {
            "channel_follower_count": 800,
            "entries": [{"view_count": 100}, {"view_count": 200}],
        }

        def fake_flat(url, playlistend=None, lang=None):
            return None if url.endswith("/videos") else shorts_info

        with patch.object(cd, "_flat_extract", side_effect=fake_flat):
            result = cd.fetch_phase1("https://www.youtube.com/@shortsonly")

        assert result is not None
        assert result.subscriber_count == 800
        assert result.video_entries == []
        assert len(result.shorts_entries) == 2

    def test_neither_tab_usable_returns_none(self, engine):
        with patch.object(cd, "_flat_extract", return_value=None):
            assert cd.fetch_phase1("https://www.youtube.com/@dead") is None


# ---------- fetch_phase2 / cadence + post time --------------------------------


class TestFetchPhase2:
    def test_cadence_from_two_dated_points(self, engine):
        entries = [{"id": f"v{i}"} for i in range(10)]

        def fake_full(url):
            if url.endswith("v0"):
                return {"timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc).timestamp()}
            if url.endswith("v9"):
                return {"timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()}
            return None

        with patch.object(cd, "_full_extract", side_effect=fake_full):
            result = cd.fetch_phase2(entries, [])

        assert result.cadence_videos_days_per_video == pytest.approx(236 / 9, abs=1)
        assert result.cadence_shorts_days_per_video is None
        assert result.days_since_last_video >= 0
        assert result.latest_video_posted_at == datetime(2026, 8, 25, tzinfo=timezone.utc)

    def test_retries_past_private_videos(self, engine):
        entries = [{"id": "bad"}, {"id": "good0"}] + [{"id": f"v{i}"} for i in range(2, 10)]

        def fake_full(url):
            if url.endswith("bad"):
                return None
            if url.endswith("good0"):
                return {"timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc).timestamp()}
            if url.endswith("v9"):
                return {"timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()}
            return None

        with patch.object(cd, "_full_extract", side_effect=fake_full):
            result = cd.fetch_phase2(entries, [])
        assert result.cadence_videos_days_per_video is not None

    def test_falls_back_to_upload_date_without_timestamp(self, engine):
        entries = [{"id": f"v{i}"} for i in range(3)]

        def fake_full(url):
            if url.endswith("v0"):
                return {"upload_date": "20260825"}
            if url.endswith("v2"):
                return {"upload_date": "20260101"}
            return None

        with patch.object(cd, "_full_extract", side_effect=fake_full):
            result = cd.fetch_phase2(entries, [])
        assert result.latest_video_posted_at == datetime(2026, 8, 25, tzinfo=timezone.utc)

    def test_both_tabs_get_independent_cadence(self, engine):
        video_entries = [{"id": f"v{i}"} for i in range(3)]
        shorts_entries = [{"id": f"s{i}"} for i in range(3)]

        def fake_full(url):
            if url.endswith("v0"):
                return {"timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc).timestamp()}
            if url.endswith("v2"):
                return {"timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()}  # slow cadence
            if url.endswith("s0"):
                return {"timestamp": datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp()}
            if url.endswith("s2"):
                return {"timestamp": datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()}  # fast cadence
            return None

        with patch.object(cd, "_full_extract", side_effect=fake_full):
            result = cd.fetch_phase2(video_entries, shorts_entries)
        assert result.cadence_videos_days_per_video == pytest.approx(236 / 2, abs=1)
        assert result.cadence_shorts_days_per_video == pytest.approx(4 / 2, abs=1)
        # Recency still comes from whichever tab has the more recent video (videos, here).
        assert result.latest_video_posted_at == datetime(2026, 8, 25, tzinfo=timezone.utc)

    def test_no_usable_dates_returns_inf(self, engine):
        with patch.object(cd, "_full_extract", return_value=None):
            result = cd.fetch_phase2([{"id": "a"}, {"id": "b"}], [])
        assert result.days_since_last_video == float("inf")
        assert result.cadence_videos_days_per_video is None
        assert result.cadence_shorts_days_per_video is None
        assert result.latest_video_posted_at is None

    def test_fewer_than_two_entries_is_not_usable(self, engine):
        with patch.object(cd, "_full_extract", return_value={"timestamp": 1}):
            result = cd.fetch_phase2([{"id": "only-one"}], [])
        assert result.cadence_videos_days_per_video is None


# ---------- search_channel_candidates -----------------------------------------


class TestSearchChannelCandidates:
    def test_dedupes_by_channel_id(self, engine):
        info = {
            "entries": [
                {"channel_id": "UC1", "channel_url": "https://www.youtube.com/channel/UC1", "channel": "A"},
                {"channel_id": "UC1", "channel_url": "https://www.youtube.com/channel/UC1", "channel": "A"},
                {"channel_id": "UC2", "channel_url": "https://www.youtube.com/channel/UC2", "channel": "B"},
            ]
        }
        with patch.object(cd, "_flat_extract", return_value=info):
            result = cd.search_channel_candidates("nhảy thiếu nhi", "en")
        assert len(result) == 2

    def test_entries_missing_channel_fields_are_skipped(self, engine):
        info = {"entries": [{"title": "no channel info"}]}
        with patch.object(cd, "_flat_extract", return_value=info):
            assert cd.search_channel_candidates("kw", "en") == []


# ---------- suggest_keywords ----------------------------------------------------


class TestSuggestKeywords:
    def test_parses_jsonp_response(self, engine):
        raw = '["seed",[["seed suggestion one",0,[512]],["seed suggestion two",0,[512]]]]'
        jsonp = f"window.google.ac.h({raw})"
        with patch.object(cd, "_http_get_text", return_value=jsonp):
            result = cd.suggest_keywords("seed", "US", "en")
        assert result == ["seed suggestion one", "seed suggestion two"]

    def test_network_failure_returns_empty_list(self, engine):
        with patch.object(cd, "_http_get_text", return_value=None):
            assert cd.suggest_keywords("seed", "US", "en") == []

    def test_malformed_response_returns_empty_list(self, engine):
        with patch.object(cd, "_http_get_text", return_value="not jsonp at all"):
            assert cd.suggest_keywords("seed", "US", "en") == []


# ---------- run_scan (DB-backed) -----------------------------------------------


class TestRunScan:
    def _fake_phase1(self):
        return cd.Phase1Result(
            subscriber_count=5000,
            median_view_count=10000,
            video_entries=[{"id": "v0"}, {"id": "v1"}],
            shorts_entries=[{"id": "s0"}, {"id": "s1"}],
            channel_description="A channel about things",
            median_duration_seconds=600,
        )

    def _fake_phase2(self):
        return cd.Phase2Result(
            days_since_last_video=1.0,
            cadence_videos_days_per_video=1.0,
            cadence_shorts_days_per_video=0.5,
            latest_video_posted_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        )

    def _make_run(self, keywords=("kw",)) -> int:
        import json as _json
        from sqlmodel import Session

        with Session(_db.engine) as s:
            run = DiscoveryScanRun(
                keywords_key=cd.normalize_keyword_set(list(keywords)),
                keywords_json=_json.dumps(list(keywords)),
                market_gl="US",
                market_hl="en",
                status="running",
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            return run.id

    def test_dedupes_against_existing_pipeline(self, engine):
        from sqlmodel import Session

        with Session(_db.engine) as s:
            s.add(
                ChannelPipeline(
                    name="existing",
                    channel_url="https://www.youtube.com/@already-here/videos",
                    fetch_count=1,
                    interval_value=1,
                    interval_unit="days",
                    first_fire_at=_db.now_utc(),
                    next_fire_at=_db.now_utc(),
                    caption_template="{title}",
                )
            )
            s.commit()

        run_id = self._make_run()
        search_result = [
            {"channel_id": "UC-existing", "channel_url": "https://www.youtube.com/@already-here", "channel_title": "Already Here"},
        ]
        with patch.object(cd, "search_channel_candidates", return_value=search_result), \
             patch.object(cd, "fetch_phase1") as mock_p1:
            summary = cd.run_scan(run_id, ["kw"], "US", "en")

        assert summary.channels_total == 0
        mock_p1.assert_not_called()

    def test_skips_previously_actioned_candidate_without_rescanning(self, engine):
        from sqlmodel import Session

        with Session(_db.engine) as s:
            s.add(
                DiscoveryCandidate(
                    channel_id="UC-skip",
                    channel_url="https://www.youtube.com/@skipme",
                    channel_title="Skip Me",
                    status="skipped",
                )
            )
            s.commit()

        run_id = self._make_run()
        search_result = [
            {"channel_id": "UC-skip", "channel_url": "https://www.youtube.com/@skipme", "channel_title": "Skip Me"},
        ]
        with patch.object(cd, "search_channel_candidates", return_value=search_result), \
             patch.object(cd, "fetch_phase1") as mock_p1:
            summary = cd.run_scan(run_id, ["kw"], "US", "en")

        assert summary.channels_total == 0
        mock_p1.assert_not_called()

    def test_new_candidate_is_saved_with_score(self, engine):
        run_id = self._make_run()
        search_result = [
            {"channel_id": "UC-new", "channel_url": "https://www.youtube.com/@newchannel", "channel_title": "New Channel"},
        ]
        with patch.object(cd, "search_channel_candidates", return_value=search_result), \
             patch.object(cd, "fetch_phase1", return_value=self._fake_phase1()), \
             patch.object(cd, "fetch_phase2", return_value=self._fake_phase2()):
            summary = cd.run_scan(run_id, ["kw"], "US", "en")

        assert summary.channels_total == 1
        assert summary.candidates_found == 1

        from sqlmodel import Session, select

        with Session(_db.engine) as s:
            result_row = s.exec(
                select(DiscoveryScanResult).where(DiscoveryScanResult.channel_id == "UC-new")
            ).first()
            run_row = s.get(DiscoveryScanRun, run_id)
        assert result_row is not None
        assert result_row.score > 0
        assert result_row.cadence_videos_days_per_video == 1.0
        assert result_row.cadence_shorts_days_per_video == 0.5
        # SQLite drops tzinfo on round-trip — compare naive.
        assert result_row.latest_video_posted_at == datetime(2026, 8, 25)
        assert run_row.status == "done"
        assert run_row.candidates_found == 1

    def test_rescanning_replaces_old_results_for_same_run(self, engine):
        run_id = self._make_run()
        search_result = [
            {"channel_id": "UC-a", "channel_url": "https://www.youtube.com/@a", "channel_title": "A"},
        ]
        with patch.object(cd, "search_channel_candidates", return_value=search_result), \
             patch.object(cd, "fetch_phase1", return_value=self._fake_phase1()), \
             patch.object(cd, "fetch_phase2", return_value=self._fake_phase2()):
            cd.run_scan(run_id, ["kw"], "US", "en")

        search_result_2 = [
            {"channel_id": "UC-b", "channel_url": "https://www.youtube.com/@b", "channel_title": "B"},
        ]
        with patch.object(cd, "search_channel_candidates", return_value=search_result_2), \
             patch.object(cd, "fetch_phase1", return_value=self._fake_phase1()), \
             patch.object(cd, "fetch_phase2", return_value=self._fake_phase2()):
            # Simulate the router's "reset" step: real re-scan deletes old
            # results for this run_id before calling run_scan again.
            from sqlmodel import Session, delete

            with Session(_db.engine) as s:
                s.exec(delete(DiscoveryScanResult).where(DiscoveryScanResult.scan_run_id == run_id))
                s.commit()
            cd.run_scan(run_id, ["kw"], "US", "en")

        from sqlmodel import Session, select

        with Session(_db.engine) as s:
            rows = s.exec(select(DiscoveryScanResult).where(DiscoveryScanResult.scan_run_id == run_id)).all()
        assert [r.channel_id for r in rows] == ["UC-b"]

    def test_silent_channel_is_rejected_after_phase2(self, engine):
        settings.set_setting("discovery.silent_months", "6")
        run_id = self._make_run()
        search_result = [
            {"channel_id": "UC-silent", "channel_url": "https://www.youtube.com/@silentchannel", "channel_title": "Silent"},
        ]
        silent_phase2 = cd.Phase2Result(
            days_since_last_video=400.0,
            cadence_videos_days_per_video=None,
            cadence_shorts_days_per_video=None,
            latest_video_posted_at=None,
        )
        with patch.object(cd, "search_channel_candidates", return_value=search_result), \
             patch.object(cd, "fetch_phase1", return_value=self._fake_phase1()), \
             patch.object(cd, "fetch_phase2", return_value=silent_phase2):
            summary = cd.run_scan(run_id, ["kw"], "US", "en")

        assert summary.candidates_found == 0
        assert summary.candidates_rejected == 1

    def test_progress_callback_invoked(self, engine):
        run_id = self._make_run()
        search_result = [
            {"channel_id": "UC-p", "channel_url": "https://www.youtube.com/@progresschannel", "channel_title": "P"},
        ]
        calls = []
        with patch.object(cd, "search_channel_candidates", return_value=search_result), \
             patch.object(cd, "fetch_phase1", return_value=self._fake_phase1()), \
             patch.object(cd, "fetch_phase2", return_value=self._fake_phase2()):
            cd.run_scan(run_id, ["kw"], "US", "en", progress_cb=lambda done, total: calls.append((done, total)))
        assert calls[0] == (0, 1)
        assert calls[-1] == (1, 1)

    def test_failure_finalizes_run_as_error_and_reraises(self, engine):
        run_id = self._make_run()
        with patch.object(cd, "search_channel_candidates", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                cd.run_scan(run_id, ["kw"], "US", "en")

        from sqlmodel import Session

        with Session(_db.engine) as s:
            run_row = s.get(DiscoveryScanRun, run_id)
        assert run_row.status == "error"
        assert "boom" in run_row.error_text
