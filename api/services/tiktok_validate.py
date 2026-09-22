"""Server-side validation of a user-supplied TikTok URL.

Confirms the URL points to a TikTok domain. yt-dlp handles whatever path
form the user gave (a creator profile `/@handle`, a single video, a short
`vm.tiktok.com` link) so we deliberately do NOT restrict path — same stance
as api.services.youtube_validate.
"""
from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
})

MAX_URL_LEN = 2048


def is_tiktok_url(url: str) -> bool:
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        return False
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return u.netloc in ALLOWED_HOSTS
