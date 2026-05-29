"""Telegram Bot API client — send_message, getUpdates-based chat discovery.

Security: follow_redirects=False, host asserted to api.telegram.org.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
# Numeric only — private channels/supergroups use -100xxxxxxxxxx.
_CHAT_ID_RE = re.compile(r"^-?\d+$")
_ALLOWED_HOST = "api.telegram.org"
_GROUP_LIKE_TYPES = frozenset({"group", "supergroup", "channel", "private"})

TELEGRAM_TEST_MESSAGE = (
    "🔔 Test from TikTok Uploader — Telegram is configured correctly!"
)


class TelegramApiError(Exception):
    """Telegram Bot API returned an error or an unexpected HTTP response."""


def _method_url(bot_token: str, method: str) -> str:
    url = httpx.URL(f"https://api.telegram.org/bot{bot_token}/{method}")
    if url.host != _ALLOWED_HOST:
        raise ValueError(f"Constructed URL host {url.host!r} != {_ALLOWED_HOST!r}")
    return str(url)


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a text message via Telegram Bot API.

    Returns True on success, False on network/API error.
    Raises ValueError if token or chat_id format is invalid.
    """
    if not _BOT_TOKEN_RE.match(bot_token):
        raise ValueError("telegram bot_token format invalid")
    if not _CHAT_ID_RE.match(chat_id):
        raise ValueError("telegram chat_id format invalid")

    url = _method_url(bot_token, "sendMessage")

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


def _chats_from_updates_result(result: list[Any]) -> list[dict[str, Any]]:
    """Build deduped group/channel chat rows from getUpdates `result` list."""
    ordered: dict[int, dict[str, Any]] = {}
    for update in result:
        if not isinstance(update, dict):
            continue
        for key in ("message", "channel_post", "my_chat_member"):
            block = update.get(key)
            if not isinstance(block, dict):
                continue
            chat = block.get("chat")
            if not isinstance(chat, dict):
                continue
            ctype = chat.get("type")
            if ctype not in _GROUP_LIKE_TYPES:
                continue
            cid = chat.get("id")
            if cid is None:
                continue
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            if cid_int in ordered:
                continue
            title = chat.get("title") or chat.get("first_name") or chat.get("username") or str(cid_int)
            ordered[cid_int] = {"id": cid_int, "title": str(title), "type": str(ctype)}
    return list(ordered.values())


def detect_chats(bot_token: str) -> list[dict[str, Any]]:
    """Poll Telegram for recent updates and list unique group/channel chats.

    Uses ``getUpdates`` only (limit 100). The bot should have recent activity
    in a group, supergroup, or channel so those chats appear in the update
    buffer.

    Raises:
        ValueError: bot_token format invalid.
        TelegramApiError: HTTP failure or ``ok: false`` from getUpdates.
    """
    if not _BOT_TOKEN_RE.match(bot_token):
        raise ValueError("telegram bot_token format invalid")

    get_url = _method_url(bot_token, "getUpdates")

    try:
        with httpx.Client(follow_redirects=False, timeout=15) as client:
            resp = client.post(get_url, json={"limit": 100})
    except httpx.HTTPError as exc:
        logger.warning("Telegram getUpdates network error: %s", exc)
        raise TelegramApiError("getUpdates network error") from exc

    if resp.status_code != 200:
        logger.warning("Telegram getUpdates HTTP status=%s", resp.status_code)
        raise TelegramApiError("getUpdates HTTP error")

    try:
        data = resp.json()
    except ValueError:
        logger.warning("Telegram getUpdates invalid JSON body")
        raise TelegramApiError("getUpdates invalid response")

    if not data.get("ok"):
        logger.warning("Telegram getUpdates returned ok=false")
        raise TelegramApiError("getUpdates API error")

    result = data.get("result")
    if not isinstance(result, list):
        return []

    chats = _chats_from_updates_result(result)

    if not chats:
        # Check webhook — getUpdates returns nothing when a webhook is active
        try:
            wh_url = _method_url(bot_token, "getWebhookInfo")
            with httpx.Client(follow_redirects=False, timeout=10) as client:
                wh_resp = client.get(wh_url)
            if wh_resp.status_code == 200:
                wh_data = wh_resp.json()
                if wh_data.get("result", {}).get("url"):
                    raise TelegramApiError(
                        "Bot có webhook đang hoạt động — getUpdates không trả về tin nhắn. "
                        "Xóa webhook trước, gửi một tin nhắn cho bot, rồi thử lại."
                    )
        except TelegramApiError:
            raise
        except Exception:
            pass

        if not result:
            raise TelegramApiError(
                "Không tìm thấy tin nhắn gần đây. "
                "Hãy gửi một tin nhắn cho bot rồi bấm Auto-detect lại."
            )

    return chats
