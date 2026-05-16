"""Telegram Bot API client — send_message only.

Security: follow_redirects=False, host asserted to api.telegram.org.
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
_CHAT_ID_RE = re.compile(r"^(-?\d+|@[A-Za-z0-9_]{5,32})$")
_ALLOWED_HOST = "api.telegram.org"


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a text message via Telegram Bot API.

    Returns True on success, False on network/API error.
    Raises ValueError if token or chat_id format is invalid.
    """
    if not _BOT_TOKEN_RE.match(bot_token):
        raise ValueError("telegram bot_token format invalid")
    if not _CHAT_ID_RE.match(chat_id):
        raise ValueError("telegram chat_id format invalid")

    url = httpx.URL(f"https://api.telegram.org/bot{bot_token}/sendMessage")
    if url.host != _ALLOWED_HOST:
        raise ValueError(f"Constructed URL host {url.host!r} != {_ALLOWED_HOST!r}")

    try:
        with httpx.Client(follow_redirects=False, timeout=10) as client:
            resp = client.post(
                str(url),
                json={"chat_id": chat_id, "text": text},
            )
        if resp.status_code == 200:
            return True
        logger.warning(
            "Telegram sendMessage failed: status=%d body=%s",
            resp.status_code,
            resp.text[:200],
        )
        return False
    except httpx.HTTPError as exc:
        logger.warning("Telegram network error: %s", exc)
        return False
