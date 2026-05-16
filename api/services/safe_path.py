"""Path validation helpers for untrusted user input.

`validate_username` rejects strings outside the strict TikTok-username
character set. `safe_join_under` builds an absolute path and refuses any
result that escapes the configured base directory.

Both are defense-in-depth: callers should still validate at the API layer.
"""
from __future__ import annotations

import os
import re

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,32}$")


def validate_username(s: str) -> str:
    if not isinstance(s, str) or not _USERNAME_RE.match(s):
        raise ValueError(f"invalid username: {s!r}")
    return s


def safe_join_under(base: str, *parts: str) -> str:
    base_abs = os.path.abspath(base)
    joined = os.path.abspath(os.path.join(base_abs, *parts))
    try:
        common = os.path.commonpath([base_abs, joined])
    except ValueError:
        raise ValueError(f"path escapes base: {joined!r} not under {base_abs!r}")
    if common != base_abs:
        raise ValueError(f"path escapes base: {joined!r} not under {base_abs!r}")
    return joined
