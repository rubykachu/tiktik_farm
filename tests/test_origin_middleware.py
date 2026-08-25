"""Unit tests for api.middleware.origin.

Mutating methods (POST/PUT/PATCH/DELETE) must reject requests whose Origin
or Referer header is not in the localhost whitelist. GET / HEAD / OPTIONS
pass through unchanged.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware.origin import LOCAL_ORIGIN, WEBAPP_PORT, OriginCheckMiddleware


def _make_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(OriginCheckMiddleware)

    @app.get("/g")
    def g():
        return {"ok": True}

    @app.post("/p")
    def p():
        return {"ok": True}

    @app.delete("/d/{id}")
    def d(id: int):
        return {"ok": True}

    return TestClient(app)


class TestGetUnchecked:
    def test_get_without_origin_passes(self):
        r = _make_client().get("/g")
        assert r.status_code == 200

    def test_get_with_external_origin_passes(self):
        r = _make_client().get("/g", headers={"Origin": "http://evil.com"})
        assert r.status_code == 200


class TestPostChecked:
    def test_post_without_origin_or_referer_is_403(self):
        r = _make_client().post("/p")
        assert r.status_code == 403
        assert r.json()["code"] == "INVALID_ORIGIN"

    def test_post_with_evil_origin_is_403(self):
        r = _make_client().post("/p", headers={"Origin": "http://evil.com"})
        assert r.status_code == 403

    def test_post_with_localhost_origin_passes(self):
        r = _make_client().post("/p", headers={"Origin": LOCAL_ORIGIN})
        assert r.status_code == 200

    def test_post_with_loopback_localhost_origin_passes(self):
        r = _make_client().post("/p", headers={"Origin": f"http://localhost:{WEBAPP_PORT}"})
        assert r.status_code == 200

    def test_post_with_localhost_referer_passes_when_origin_missing(self):
        r = _make_client().post("/p", headers={"Referer": LOCAL_ORIGIN + "/some/page"})
        assert r.status_code == 200


class TestDeleteChecked:
    def test_delete_without_origin_is_403(self):
        r = _make_client().delete("/d/1")
        assert r.status_code == 403

    def test_delete_with_localhost_origin_passes(self):
        r = _make_client().delete("/d/1", headers={"Origin": f"http://localhost:{WEBAPP_PORT}"})
        assert r.status_code == 200


class TestOptions:
    def test_options_preflight_passes_without_origin(self):
        # OPTIONS used for CORS preflight; CORS middleware handles its own checks.
        # Origin check should not 403 here.
        r = _make_client().options("/p", headers={"Origin": "http://evil.com"})
        # 405 (no OPTIONS handler) or 200/204 from CORS — but NOT 403 from our middleware.
        assert r.status_code != 403
