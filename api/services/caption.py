"""Caption template parsing, validation, and rendering.

Uses re.sub with explicit pattern — NOT str.format (prevents format-string
injection from untrusted video titles).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

ALLOWED_VARS = frozenset({"title", "title_hashtag", "now", "ai_title"})
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_RENDER_RE = re.compile(r"\{(title|title_hashtag|now|ai_title)\}")
_AI_TITLE_RE = re.compile(r"\{ai_title\}")
_HASHTAG_RE = re.compile(r"#\w+")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")

CAPTION_MAX = 2200
_TZ_SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")


def find_unknown_vars(template: str) -> list[str]:
    """Return placeholders in `template` that are not in the whitelist.

    Used by /api/pipelines POST + PATCH validators.
    """
    found = _PLACEHOLDER_RE.findall(template)
    return sorted({v for v in found if v not in ALLOWED_VARS})


def _extract_hashtags(title: str) -> str:
    """Extract #hashtag tokens from title, space-joined."""
    return " ".join(_HASHTAG_RE.findall(title))


def template_uses_ai_title(template: str) -> bool:
    return bool(_AI_TITLE_RE.search(template))


def render_caption(
    template: str,
    title: str = "",
    now: Optional[datetime] = None,
    ai_title: str = "",
) -> str:
    """Render a caption template with variable substitution + hashtag dedupe.

    Raises ValueError if template contains unknown variables.
    """
    unknown = find_unknown_vars(template)
    if unknown:
        raise ValueError(
            f"unknown caption variable(s): {', '.join(unknown)}"
        )

    if now is None:
        now = datetime.now(tz=_TZ_SAIGON)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_TZ_SAIGON)

    title_hashtag = _extract_hashtags(title)
    now_str = now.astimezone(_TZ_SAIGON).strftime("%Y-%m-%d %H:%M")

    vars_map = {
        "title": title,
        "title_hashtag": title_hashtag,
        "now": now_str,
        "ai_title": ai_title,
    }

    text = _RENDER_RE.sub(lambda m: vars_map[m.group(1)], template)

    seen: set[str] = set()

    def _keep_hashtag(m: re.Match) -> str:
        tok = m.group(0)
        key = tok.lower()
        if key in seen:
            return ""
        seen.add(key)
        return tok

    text = _HASHTAG_RE.sub(_keep_hashtag, text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()

    return text[:CAPTION_MAX]
