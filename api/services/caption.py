"""Caption template parsing + validation.

Render itself lands in task D1; this module ships the *validator* used by
the pipeline-create endpoint so unknown placeholders fail at save time
with a 400, never at upload time.
"""
from __future__ import annotations

import re

ALLOWED_VARS = frozenset({"title", "title_hashtag", "now"})
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def find_unknown_vars(template: str) -> list[str]:
    """Return placeholders in `template` that are not in the whitelist.

    Used by /api/pipelines POST + PATCH validators.
    """
    found = _PLACEHOLDER_RE.findall(template)
    return sorted({v for v in found if v not in ALLOWED_VARS})
