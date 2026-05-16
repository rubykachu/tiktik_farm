"""Convert + validate Chrome-DevTools-style JSON cookies into the pickle
format `tiktok_uploader.cookies` consumes.

Used by both the CLI script `scripts/migrate-json-cookie-editor.py` and the
`POST /api/accounts/import-json` endpoint.
"""
from __future__ import annotations

import json
import os
import pickle
import stat

MAX_FILE_BYTES = 256 * 1024
MAX_ENTRIES = 200

_SAMESITE_MAP = {
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "None",
    None: "None",
}


class CookieImportError(ValueError):
    """Raised when the JSON payload fails validation."""


def convert_cookie(src: dict) -> dict:
    if "name" not in src or "value" not in src:
        raise CookieImportError("cookie entry missing name/value")
    out = {
        "name": src["name"],
        "value": src["value"],
        "domain": src.get("domain", ".tiktok.com"),
        "path": src.get("path", "/"),
        "secure": bool(src.get("secure", True)),
        "httpOnly": bool(src.get("httpOnly", False)),
    }
    if "expirationDate" in src and src["expirationDate"] is not None:
        out["expiry"] = int(src["expirationDate"])
    raw_ss = src.get("sameSite")
    key = raw_ss.lower() if isinstance(raw_ss, str) else raw_ss
    ss = _SAMESITE_MAP.get(key, "Strict")
    if ss == "None":
        # tiktok_uploader.cookies rewrites "None" → "Strict" on load anyway.
        ss = "Strict"
    out["sameSite"] = ss
    return out


def parse_and_validate(raw_bytes: bytes) -> list[dict]:
    """Decode JSON bytes, enforce size/structure caps, return cookie dicts
    in pickle-ready format. Raises CookieImportError on any violation."""
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise CookieImportError(
            f"file size {len(raw_bytes)} exceeds {MAX_FILE_BYTES} byte cap"
        )
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CookieImportError(f"malformed JSON: {e}")
    if not isinstance(data, list):
        raise CookieImportError("payload must be a JSON list of cookie objects")
    if len(data) > MAX_ENTRIES:
        raise CookieImportError(
            f"too many entries: {len(data)} > {MAX_ENTRIES}"
        )
    converted = [convert_cookie(c) for c in data if isinstance(c, dict) and c.get("name")]
    has_session = any(
        c.get("name") == "sessionid" and c.get("value") for c in converted
    )
    if not has_session:
        raise CookieImportError("no sessionid cookie with a value found")
    return converted


def save_cookies_secure(cookie_path: str, cookies: list[dict]) -> None:
    """Pickle-dump cookies and chmod the resulting file to 0600."""
    os.makedirs(os.path.dirname(cookie_path), exist_ok=True)
    with open(cookie_path, "wb") as f:
        pickle.dump(cookies, f)
    os.chmod(cookie_path, stat.S_IRUSR | stat.S_IWUSR)
