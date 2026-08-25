"""yt-dlp options shared by every download path.

YouTube periodically breaks one of yt-dlp's player clients. The fix is almost
always a newer yt-dlp (see the floor in requirements.txt), but when a release
is broken and an upgrade isn't available yet, YTDLP_PLAYER_CLIENTS lets an
operator pin working clients without a code change — e.g. "tv,web_safari".
Empty (the default) leaves yt-dlp's own per-release defaults alone, which is
what should normally be used.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def extractor_args() -> dict:
    """Return the `extractor_args` opt, or {} when nothing is configured."""
    raw = (os.environ.get("YTDLP_PLAYER_CLIENTS") or "").strip()
    if not raw:
        return {}
    clients = [c.strip() for c in raw.split(",") if c.strip()]
    if not clients:
        return {}
    log.info("Overriding yt-dlp youtube player_client with %s", clients)
    return {"extractor_args": {"youtube": {"player_client": clients}}}
