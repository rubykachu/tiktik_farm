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

def _parse_host_origin(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        u = urlparse(value)
        return f"{u.scheme}://{u.netloc}"
    return f"https://{value}"


_host = os.environ.get("HOST", "")
_extra = {_parse_host_origin(_host)} - {""} if _host else set()

ALLOWED_ORIGINS = frozenset({
    "http://127.0.0.1:3000",
    "http://localhost:3000",
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
