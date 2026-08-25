"""Tests for GET /api/pipelines/{id}/runs and /runs/{id}/videos endpoints."""
import pytest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import api.db as _api_db
from api.db import make_engine, override_engine, now_utc
from api.main import app
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
from api.middleware.origin import LOCAL_ORIGIN
from api.pagination import DAYS_PER_RESPONSE, RUNS_PER_DAY


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    engine = make_engine(f"sqlite:///{db_path}")
    override_engine(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    override_engine(make_engine("sqlite:///"))


@pytest.fixture
def client():
    return TestClient(app, headers={"Origin": LOCAL_ORIGIN})


def _seed():
    now = now_utc()
    with Session(_api_db.engine) as s:
        acct = Account(id=1, username="user1", cookie_path="/tmp/c", has_valid_session=True)
        s.add(acct)

        p = ChannelPipeline(
            name="pipe1",
            channel_url="https://www.youtube.com/@ch/videos",
            fetch_count=3,
            interval_value=6,
            interval_unit="hours",
            first_fire_at=now,
            next_fire_at=now,
            state="active",
            caption_template="{title}",
            upload_options_json="{}",
            account_ids_json="[1]",
            created_at=now,
            updated_at=now,
        )
        s.add(p)
        s.commit()
        s.refresh(p)

        run = PipelineRun(
            pipeline_id=p.id,
            started_at=now - timedelta(minutes=10),
            finished_at=now,
            trigger_reason="manual",
            pulled_count=3,
            uploaded_count=2,
            failed_count=1,
        )
        s.add(run)
        s.commit()
        s.refresh(run)

        v = VideoUpload(
            pipeline_id=p.id,
            run_id=run.id,
            account_id=1,
            youtube_video_id="vid1",
            youtube_url="https://youtube.com/watch?v=vid1",
            source_title="Title 1",
            status="succeeded",
            attempt_count=1,
            created_at=now,
        )
        s.add(v)
        s.commit()
        return {"pipeline_id": p.id, "run_id": run.id, "video_id": v.id}


class TestListRuns:
    def test_happy(self, client):
        ids = _seed()
        resp = client.get(f"/api/pipelines/{ids['pipeline_id']}/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["days"]) == 1
        day = data["days"][0]
        assert day["total"] == 1
        assert day["items"][0]["trigger_reason"] == "manual"
        assert day["items"][0]["pulled_count"] == 3

    def test_empty_runs(self, client):
        ids = _seed()
        with Session(_api_db.engine) as s:
            p2 = ChannelPipeline(
                name="empty",
                channel_url="https://www.youtube.com/@empty/videos",
                fetch_count=1,
                interval_value=1,
                interval_unit="hours",
                first_fire_at=now_utc(),
                next_fire_at=now_utc(),
                state="active",
                caption_template="{title}",
                upload_options_json="{}",
                account_ids_json="[1]",
                created_at=now_utc(),
                updated_at=now_utc(),
            )
            s.add(p2)
            s.commit()
            s.refresh(p2)
            p2_id = p2.id

        resp = client.get(f"/api/pipelines/{p2_id}/runs")
        assert resp.status_code == 200
        assert resp.json()["days"] == []

    def test_pagination_rejects_invalid_limit(self, client):
        ids = _seed()
        url = f"/api/pipelines/{ids['pipeline_id']}/runs"
        assert client.get(url, params={"limit": "0"}).status_code == 422
        assert client.get(url, params={"limit": "999"}).status_code == 422
        assert client.get(url, params={"offset": "-1"}).status_code == 422

    def test_pipeline_not_found(self, client):
        resp = client.get("/api/pipelines/999/runs")
        assert resp.status_code == 404


def _bare_pipeline(**kw) -> int:
    now = now_utc()
    defaults = dict(
        name="p", channel_url="https://www.youtube.com/@x/videos", fetch_count=1,
        interval_value=1, interval_unit="hours", first_fire_at=now, next_fire_at=now,
        state="active", caption_template="{title}", upload_options_json="{}",
        account_ids_json="[]",
    )
    defaults.update(kw)
    with Session(_api_db.engine) as s:
        p = ChannelPipeline(**defaults)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


def _add_runs(pipeline_id: int, starts) -> None:
    with Session(_api_db.engine) as s:
        for start in starts:
            s.add(PipelineRun(
                pipeline_id=pipeline_id, started_at=start,
                finished_at=start + timedelta(minutes=1), trigger_reason="scheduled",
            ))
        s.commit()


class TestRunsGroupedByDay:
    """Runs are bucketed by the operator's calendar day so a day is never split
    across a page boundary — a day heading that disagrees with the rows under
    it is worse than an extra click."""

    def test_day_boundary_follows_the_operator_timezone(self, client):
        """Asia/Ho_Chi_Minh is UTC+7, so 17:00 UTC is already the next day.
        Bucketing on the raw UTC date would file it under the previous one."""
        pid = _bare_pipeline()
        _add_runs(pid, [
            datetime(2026, 8, 20, 16, 59),  # 23:59 local, 20 Aug
            datetime(2026, 8, 20, 17, 0),   # 00:00 local, 21 Aug
        ])

        days = client.get(f"/api/pipelines/{pid}/runs").json()["days"]

        assert [d["date"] for d in days] == ["2026-08-21", "2026-08-20"]
        assert days[0]["total"] == 1
        assert days[1]["total"] == 1

    def test_a_day_reports_its_full_count_while_capping_rows(self, client):
        pid = _bare_pipeline()
        base = datetime(2026, 8, 25, 3, 0)
        _add_runs(pid, [base - timedelta(minutes=2 * i) for i in range(80)])

        day = client.get(f"/api/pipelines/{pid}/runs").json()["days"][0]

        assert day["total"] == 80
        assert len(day["items"]) == RUNS_PER_DAY

    def test_days_are_capped_and_newest_first(self, client):
        pid = _bare_pipeline()
        base = datetime(2026, 8, 25, 3, 0)
        _add_runs(pid, [base - timedelta(days=d) for d in range(40)])

        days = client.get(f"/api/pipelines/{pid}/runs").json()["days"]

        assert len(days) == DAYS_PER_RESPONSE
        assert [d["date"] for d in days] == sorted(
            (d["date"] for d in days), reverse=True
        )
        assert days[0]["date"] == "2026-08-25"

    def test_day_totals_cover_the_whole_day_not_the_loaded_slice(self, client):
        """The day heading reports these; summing only the returned rows would
        under-report a busy day."""
        pid = _bare_pipeline()
        base = datetime(2026, 8, 25, 3, 0)
        with Session(_api_db.engine) as s:
            for i in range(50):
                start = base - timedelta(minutes=i)
                s.add(PipelineRun(
                    pipeline_id=pid, started_at=start,
                    finished_at=start + timedelta(minutes=1),
                    trigger_reason="scheduled", uploaded_count=1,
                ))
            s.commit()

        day = client.get(f"/api/pipelines/{pid}/runs").json()["days"][0]

        assert len(day["items"]) == RUNS_PER_DAY
        assert day["total"] == 50
        assert day["uploaded_total"] == 50

    def test_no_runs_returns_no_days(self, client):
        pid = _bare_pipeline()

        assert client.get(f"/api/pipelines/{pid}/runs").json()["days"] == []

    def test_cap_applies_per_day_not_across_days(self, client):
        """A busy day must not starve the days below it."""
        pid = _bare_pipeline()
        base = datetime(2026, 8, 25, 3, 0)
        _add_runs(pid, [base - timedelta(minutes=i) for i in range(50)])
        _add_runs(pid, [base - timedelta(days=1, minutes=i) for i in range(5)])

        days = client.get(f"/api/pipelines/{pid}/runs").json()["days"]

        assert len(days[0]["items"]) == RUNS_PER_DAY
        assert len(days[1]["items"]) == 5

    def test_runs_of_other_pipelines_are_not_mixed_in(self, client):
        mine = _bare_pipeline(name="mine")
        theirs = _bare_pipeline(name="theirs")
        base = datetime(2026, 8, 25, 3, 0)
        _add_runs(mine, [base])
        _add_runs(theirs, [base - timedelta(minutes=1)])

        days = client.get(f"/api/pipelines/{mine}/runs").json()["days"]

        assert sum(d["total"] for d in days) == 1


class TestRunsLoadMoreWithinADay:
    def _busy_day(self) -> int:
        pid = _bare_pipeline()
        base = datetime(2026, 8, 25, 3, 0)
        _add_runs(pid, [base - timedelta(minutes=i) for i in range(70)])
        return pid

    def test_offset_continues_where_the_group_stopped(self, client):
        pid = self._busy_day()
        url = f"/api/pipelines/{pid}/runs"

        first = client.get(url, params={"date": "2026-08-25"}).json()
        second = client.get(url, params={"date": "2026-08-25", "offset": 30}).json()

        assert first["total"] == second["total"] == 70
        assert len(first["items"]) == 30
        assert len(second["items"]) == 30
        overlap = {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
        assert overlap == set()

    def test_last_page_is_short(self, client):
        pid = self._busy_day()

        body = client.get(
            f"/api/pipelines/{pid}/runs", params={"date": "2026-08-25", "offset": 60}
        ).json()

        assert len(body["items"]) == 10

    def test_unknown_day_is_empty_not_an_error(self, client):
        pid = self._busy_day()

        body = client.get(
            f"/api/pipelines/{pid}/runs", params={"date": "1999-01-01"}
        ).json()

        assert body == {"date": "1999-01-01", "items": [], "total": 0, "offset": 0}

    def test_day_scope_uses_the_same_boundary_as_the_grouping(self, client):
        pid = _bare_pipeline()
        _add_runs(pid, [
            datetime(2026, 8, 20, 16, 59),
            datetime(2026, 8, 20, 17, 0),
        ])

        body = client.get(
            f"/api/pipelines/{pid}/runs", params={"date": "2026-08-21"}
        ).json()

        assert body["total"] == 1
        assert body["items"][0]["started_at"].startswith("2026-08-20T17:00")


class TestListRunVideos:
    def test_happy(self, client):
        ids = _seed()
        resp = client.get(
            f"/api/pipelines/{ids['pipeline_id']}/runs/{ids['run_id']}/videos"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["youtube_video_id"] == "vid1"
        assert data[0]["status"] == "succeeded"

    def test_pipeline_not_found(self, client):
        resp = client.get("/api/pipelines/999/runs/1/videos")
        assert resp.status_code == 404

    def test_run_not_found(self, client):
        ids = _seed()
        resp = client.get(f"/api/pipelines/{ids['pipeline_id']}/runs/999/videos")
        assert resp.status_code == 404

    def test_run_wrong_pipeline(self, client):
        ids = _seed()
        now = now_utc()
        with Session(_api_db.engine) as s:
            p2 = ChannelPipeline(
                name="other",
                channel_url="https://www.youtube.com/@other/videos",
                fetch_count=1,
                interval_value=1,
                interval_unit="hours",
                first_fire_at=now,
                next_fire_at=now,
                state="active",
                caption_template="{title}",
                upload_options_json="{}",
                account_ids_json="[1]",
                created_at=now,
                updated_at=now,
            )
            s.add(p2)
            s.commit()
            s.refresh(p2)
            p2_id = p2.id

        # run belongs to pipeline 1, accessing via pipeline 2 → 404
        resp = client.get(f"/api/pipelines/{p2_id}/runs/{ids['run_id']}/videos")
        assert resp.status_code == 404
