"""Per-platform discovery sources. See base.py for the interface each one
implements; api.services.channel_discovery is what drives them."""
from __future__ import annotations

from types import ModuleType

from api.services.discovery_sources import douyin, tiktok, youtube

# Order is what the webapp's platform picker shows.
SOURCES: dict[str, ModuleType] = {
    "youtube": youtube,
    "tiktok": tiktok,
    "douyin": douyin,
}

PLATFORMS = tuple(SOURCES)


def get(platform: str) -> ModuleType:
    try:
        return SOURCES[platform]
    except KeyError:
        raise ValueError(f"Unknown discovery platform: {platform}") from None
