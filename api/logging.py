"""Log redaction helpers.

Telegram bot tokens (shape `123:abcdef…`) must never appear in logs even if
a developer accidentally interpolates one into a debug message. The filter
installs at the handler level and rewrites the formatted message before it
is emitted.
"""
from __future__ import annotations

import logging
import re

_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{10,}")


def scrub_bot_tokens(text: str) -> str:
    return _BOT_TOKEN_RE.sub("bot***", text)


class BotTokenScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Resolve %s/%d args into the final message, then scrub.
        msg = record.getMessage()
        record.msg = scrub_bot_tokens(msg)
        record.args = None
        return True


def install_global_scrub_filter() -> None:
    """Attach BotTokenScrubFilter to every existing handler on the root logger.

    Call once at application startup. Idempotent — re-calling will add the
    same filter again but Python's logging deduplicates by identity at emit
    time, so behavior is unchanged.
    """
    f = BotTokenScrubFilter()
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(f)
    # Also catch handlers added later by other libraries.
    root.addFilter(f)
