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
_API_KEY_KV_RE = re.compile(r"(api_key)\s*=\s*[^\s,;]+", re.IGNORECASE)
# Catches Authorization: Bearer X, x-api-key: X, "api_key":"X", api-key=X.
_AUTH_HEADER_RE = re.compile(
    r"""(?ix)
    (authorization|x-api-key|api[-_]?key)
    \s*[:=]\s*
    "?(?:bearer\s+)?[A-Za-z0-9._\-]+"?
    """
)
# Bare provider-prefixed keys inside free text. Charset includes `.` because
# OpenAI's project-key format (sk-proj-...) embeds dots between secret
# segments; without the dot the regex stops at the first segment and the
# trailing material — which IS the secret — survives.
_KNOWN_KEY_PREFIX_RE = re.compile(
    r"\b("
    r"sk-ant-[A-Za-z0-9._\-]{16,}"
    r"|sk-proj-[A-Za-z0-9._\-]{16,}"
    r"|sk-[A-Za-z0-9._\-]{16,}"
    r"|AIza[A-Za-z0-9._\-]{20,}"
    r")\b"
)
# JWT shape: eyJ<base64>.eyJ<base64>.<sig>. Header+payload are base64-decodable
# to subject/issuer, so all three segments must be redacted together. Caught
# before _LONG_OPAQUE_TOKEN_RE so the substitution preserves the JWT structure
# in logs ("***.***.*** redacted JWT") rather than three independent ***.
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b"
)
# Python bytes-literal exposing a credential, e.g. httpx "Illegal header value
# b'<key>'" when an Azure key reaches the wire and fails header validation.
_BYTES_LITERAL_OPAQUE_RE = re.compile(r"b'[A-Za-z0-9+/=_\-]{20,}'")
# Long opaque alphanumeric tokens (≥ 32 chars) inside error free-text. Azure
# / OpenAI keys and similar credentials all match this shape. Collateral:
# git SHAs (40 hex) and base64 cache keys also get scrubbed — acceptable
# trade-off in error messages but causes debug noise; tighten if it becomes
# a problem.
_LONG_OPAQUE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9+/=_\-]{32,}\b")


def scrub_bot_tokens(text: str) -> str:
    text = _BOT_TOKEN_RE.sub("bot***", text)
    text = _API_KEY_KV_RE.sub(r"\1=***", text)
    text = _AUTH_HEADER_RE.sub(r"\1=***", text)
    text = _KNOWN_KEY_PREFIX_RE.sub("***", text)
    text = _JWT_RE.sub("***.***.***", text)
    text = _BYTES_LITERAL_OPAQUE_RE.sub("b'***'", text)
    text = _LONG_OPAQUE_TOKEN_RE.sub("***", text)
    return text


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
