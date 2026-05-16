"""Text helpers shared across services."""
from __future__ import annotations

DEFAULT_SUFFIX = "…(truncated)"


def truncate(text: str, max_chars: int, suffix: str = DEFAULT_SUFFIX) -> str:
    """Return `text` capped to `max_chars` characters total.

    If `text` is already short enough, returns it unchanged. Otherwise
    appends `suffix` so the reader can tell content was cut. When the cap
    is smaller than the suffix itself, falls back to a plain slice so the
    cap is always respected.
    """
    if not text or max_chars <= 0:
        return "" if max_chars <= 0 else text
    if len(text) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix
