"""Origin / Referer check for mutating HTTP methods.

The webapp is served only from `127.0.0.1:3000` / `localhost:3000` (see
`docker-compose.yml` and `wiki/03-setup-docker.md`). Mutating requests
(POST/PUT/PATCH/DELETE) must originate from one of those origins so a
malicious page in another tab cannot CSRF the local API.

Safe methods (GET/HEAD/OPTIONS) are not checked — CORS handles cross-origin
read attempts at the browser layer.

Set HOST to the public hostname of this deployment (e.g. the cloudflared
tunnel URL) to allow that origin. Accepts hostname or full URL; scheme
defaults to https if omitted.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from api.services.public_url import normalize_base_url

# The port the webapp is published on. Must match the host mapping in
# docker-compose.yml — a mismatch makes every button in the UI fail the origin
# check with 403 while GETs keep working, which reads like a broken frontend.
# 2705 rather than 3000: that range collides with everything else on a dev box.
DEFAULT_WEBAPP_PORT = "2705"
WEBAPP_PORT = os.environ.get("WEBAPP_PORT") or DEFAULT_WEBAPP_PORT

LOCAL_ORIGIN = f"http://127.0.0.1:{WEBAPP_PORT}"

_host = os.environ.get("HOST", "")
_extra = {normalize_base_url(_host)} - {""} if _host else set()

ALLOWED_ORIGINS = frozenset({
    LOCAL_ORIGIN,
    f"http://localhost:{WEBAPP_PORT}",
} | _extra)

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_from_referer(referer: str) -> str | None:
    try:
        u = urlparse(referer)
        if not u.scheme or not u.netloc:
            return None
        return f"{u.scheme}://{u.netloc}"
    except Exception:
        return None


class OriginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in MUTATING_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer")
            origin = _origin_from_referer(referer) if referer else None

        if origin not in ALLOWED_ORIGINS:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "INVALID_ORIGIN",
                    "message": "Origin not in allowed list",
                },
            )
        return await call_next(request)
