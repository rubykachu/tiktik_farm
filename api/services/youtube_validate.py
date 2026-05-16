"""Server-side validation of a user-supplied YouTube URL.

Confirms the URL points to a YouTube domain. yt-dlp handles whatever path
form the user gave (channel /videos, /shorts, /podcasts, /playlists,
playlist URL, single video) so we deliberately do NOT restrict path.
"""
from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
})

MAX_URL_LEN = 2048


def is_youtube_url(url: str) -> bool:
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        return False
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return u.netloc in ALLOWED_HOSTS
