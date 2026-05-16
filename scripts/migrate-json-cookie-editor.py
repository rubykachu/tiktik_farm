"""Convert v1 JSON cookie exports into v2 pickle CookiesDir format.

v1 format: JSON array with Chrome devtools-style keys (expirationDate,
sameSite, hostOnly, storeId, session). v2 format: pickle list with the
selenium-style keys the `tiktok_uploader` package expects (name, value,
domain, path, expiry, httpOnly, secure, sameSite).

Usage:
    python scripts/migrate_v1_cookies.py <src_dir> <dest_dir>
    python scripts/migrate_v1_cookies.py ../data/accounts ./CookiesDir
"""
from __future__ import annotations

import json
import os
import pickle
import re
import sys


_SAMESITE_MAP = {
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "None",
    None: "None",
}


def convert_cookie(src: dict) -> dict:
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
    if isinstance(raw_ss, str):
        key = raw_ss.lower()
    else:
        key = raw_ss
    ss = _SAMESITE_MAP.get(key, "Strict")
    # cookies.py rewrites "None" → "Strict" on load anyway, but be explicit.
    if ss == "None":
        ss = "Strict"
    out["sameSite"] = ss
    return out


_FNAME_RE = re.compile(r"TK_cookies_(?P<user>.+)\.json$")


def main(src_dir: str, dest_dir: str) -> int:
    os.makedirs(dest_dir, exist_ok=True)
    if not os.path.isdir(src_dir):
        print(f"src dir missing: {src_dir}", file=sys.stderr)
        return 2
    n = 0
    for fname in sorted(os.listdir(src_dir)):
        m = _FNAME_RE.match(fname)
        if not m:
            continue
        username = m.group("user")
        src = os.path.join(src_dir, fname)
        with open(src, "r", encoding="utf-8") as f:
            raw = json.load(f)
        converted = [convert_cookie(c) for c in raw if c.get("name")]
        has_session = any(c["name"] == "sessionid" and c["value"] for c in converted)
        dest = os.path.join(dest_dir, f"tiktok_session-{username}.cookie")
        with open(dest, "wb") as f:
            pickle.dump(converted, f)
        n += 1
        print(f"  {fname} -> {dest}  ({len(converted)} cookies, sessionid={'yes' if has_session else 'NO'})")
    print(f"done: {n} file(s) migrated to {dest_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
