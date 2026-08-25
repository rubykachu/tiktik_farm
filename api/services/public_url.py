"""The externally reachable base URL of this deployment.

Set when the app is exposed through a tunnel or reverse proxy. Links that
leave the app (Telegram) need it; nothing else does, and it stays unset for a
purely local install.
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse


def normalize_base_url(value: str) -> str:
    """Turn "example.com" or "https://example.com/x" into "https://example.com".

    A bare host is assumed to be https — a tunnel always is.
    """
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        u = urlparse(value)
        return f"{u.scheme}://{u.netloc}" if u.netloc else ""
    return f"https://{value}"


def public_base_url() -> Optional[str]:
    """Return the public origin, or None when the app is local-only."""
    raw = os.environ.get("PUBLIC_BASE_URL") or os.environ.get("HOST") or ""
    return normalize_base_url(raw) or None
