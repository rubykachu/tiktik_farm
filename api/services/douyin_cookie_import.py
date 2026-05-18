"""Parse Cookie-Editor JSON exports from www.douyin.com.

Usage:
    cookie_dict = parse_cookie_editor_json(raw_bytes)
    yaml_cookies = to_downloader_yaml_dict(cookie_dict)
"""
from __future__ import annotations

import json

MAX_FILE_BYTES = 256 * 1024

_KEYS = ("ttwid", "msToken", "odin_tt", "passport_csrf_token", "sid_guard")


class DouyinCookieImportError(ValueError):
    """Raised when the cookie payload fails validation."""


def parse_cookie_editor_json(raw_bytes: bytes) -> dict:
    """Parse Cookie-Editor JSON array → dict with Douyin cookie keys.

    Raises DouyinCookieImportError if ttwid is missing/empty or payload is malformed.
    """
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise DouyinCookieImportError(
            f"file size {len(raw_bytes)} exceeds {MAX_FILE_BYTES} byte cap"
        )
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DouyinCookieImportError(f"malformed JSON: {e}")
    if not isinstance(data, list):
        raise DouyinCookieImportError("payload must be a JSON list of cookie objects")

    by_name = {
        entry["name"]: entry.get("value", "")
        for entry in data
        if isinstance(entry, dict) and "name" in entry
    }

    ttwid = by_name.get("ttwid", "")
    if not ttwid:
        raise DouyinCookieImportError("ttwid cookie is required and must have a non-empty value")

    return {key: by_name.get(key, "") for key in _KEYS}


def to_downloader_yaml_dict(cookie_dict: dict) -> dict:
    """Return a dict with only the keys douyin-downloader config.yml expects."""
    return {key: cookie_dict.get(key, "") for key in _KEYS}
