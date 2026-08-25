"""Telegram Bot API client — send_message, getUpdates-based chat discovery.

Security: follow_redirects=False, host asserted to api.telegram.org.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, NamedTuple, Optional

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


# A send is attempted this many times before the caller gives up. The pipeline
# deletes the downloaded video right after notifying, so a transient network
# blip must not be the reason the operator never receives it.
MAX_SEND_ATTEMPTS = 5
# Sleep between attempts: 1s, 2s, 4s, 8s.
RETRY_BACKOFF_BASE_SECONDS = 1.0
# The retry loop runs inside the pipeline tick and holds a scheduler slot, so
# it stops retrying once this much wall time has passed regardless of attempts.
RETRY_DEADLINE_SECONDS = 900.0
# Cap on the server-supplied 429 wait, so a hostile value can't stall a tick.
MAX_RETRY_AFTER_SECONDS = 60.0


class _Attempt(NamedTuple):
    ok: bool
    retryable: bool
    # Telegram's own "wait this long" from a 429, when it sent one.
    retry_after: Optional[float] = None


def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    """Telegram puts the 429 cool-off in parameters.retry_after."""
    try:
        value = resp.json().get("parameters", {}).get("retry_after")
    except ValueError:
        return None
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return min(float(value), MAX_RETRY_AFTER_SECONDS)


def _classify(resp: httpx.Response, method: str) -> _Attempt:
    if resp.status_code == 200:
        return _Attempt(ok=True, retryable=False)
    # 429 and 5xx are transient. Any other 4xx (bad chat_id, bot kicked out,
    # file too large) will fail identically on every retry.
    retryable = resp.status_code == 429 or resp.status_code >= 500
    logger.warning(
        "Telegram %s failed: status=%d retryable=%s body=%s",
        method, resp.status_code, retryable, resp.text[:200],
    )
    return _Attempt(
        ok=False,
        retryable=retryable,
        retry_after=_retry_after_seconds(resp) if resp.status_code == 429 else None,
    )


def _send_with_retry(method: str, attempt: Callable[[], _Attempt]) -> bool:
    """Run `attempt` until it succeeds, is refused, or the budget runs out."""
    started = time.monotonic()
    for i in range(MAX_SEND_ATTEMPTS):
        try:
            result = attempt()
        except httpx.HTTPError as exc:
            logger.warning("Telegram %s network error: %s", method, exc)
            result = _Attempt(ok=False, retryable=True)

        if result.ok:
            return True
        if not result.retryable or i == MAX_SEND_ATTEMPTS - 1:
            return False

        delay = result.retry_after or RETRY_BACKOFF_BASE_SECONDS * (2 ** i)
        if time.monotonic() - started + delay > RETRY_DEADLINE_SECONDS:
            logger.warning(
                "Telegram %s giving up: retry deadline %.0fs reached after %d attempt(s)",
                method, RETRY_DEADLINE_SECONDS, i + 1,
            )
            return False
        logger.info(
            "Telegram %s retry %d/%d in %.1fs",
            method, i + 1, MAX_SEND_ATTEMPTS - 1, delay,
        )
        time.sleep(delay)
    return False


def send_message(bot_token: str, chat_id: str, text: str, html: bool = False) -> bool:
    """Send a text message via Telegram Bot API.

    Set html=True to send with parse_mode=HTML; the caller is then responsible
    for escaping every value it interpolates. Retries transient failures (see
    MAX_SEND_ATTEMPTS). Returns True on success, False once the attempts or the
    deadline are exhausted. Raises ValueError if token or chat_id is malformed.
    """
    _check_credentials(bot_token, chat_id)
    url = _method_url(bot_token, "sendMessage")
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if html:
        payload["parse_mode"] = "HTML"
        # Telegram renders a preview card for the first link otherwise, which
        # buries the message under a thumbnail.
        payload["disable_web_page_preview"] = True

    def _attempt() -> _Attempt:
        with httpx.Client(follow_redirects=False, timeout=10) as client:
            resp = client.post(url, json=payload)
        return _classify(resp, "sendMessage")

    return _send_with_retry("sendMessage", _attempt)


# Bot API refuses uploads above 50 MB; callers degrade to a text-only message.
TELEGRAM_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# sendMediaGroup accepts at most 10 items per call.
TELEGRAM_MEDIA_GROUP_MAX = 10


def _check_credentials(bot_token: str, chat_id: str) -> None:
    if not _BOT_TOKEN_RE.match(bot_token):
        raise ValueError("telegram bot_token format invalid")
    if not _CHAT_ID_RE.match(chat_id):
        raise ValueError("telegram chat_id format invalid")


def _post_files(
    bot_token: str, method: str, data: dict[str, Any], file_paths: dict[str, str]
) -> bool:
    """Upload local files, reopening them on each attempt.

    The handles have to be reopened per attempt: a retry replays the request
    body, and a consumed handle would upload zero bytes.
    """
    url = _method_url(bot_token, method)

    def _attempt() -> _Attempt:
        handles = []
        try:
            files = {}
            for key, path in file_paths.items():
                fh = open(path, "rb")
                handles.append(fh)
                files[key] = (os.path.basename(path), fh)
            with httpx.Client(follow_redirects=False, timeout=300) as client:
                resp = client.post(url, data=data, files=files)
        finally:
            for fh in handles:
                fh.close()
        return _classify(resp, method)

    return _send_with_retry(method, _attempt)


def send_document(
    bot_token: str, chat_id: str, file_path: str, caption: str | None = None
) -> bool:
    """Upload one local file via sendDocument.

    Returns True on success, False on network/API error.
    Raises ValueError if token or chat_id format is invalid.
    """
    _check_credentials(bot_token, chat_id)

    data: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption[:1024]
    return _post_files(bot_token, "sendDocument", data, {"document": file_path})


def send_media_group(
    bot_token: str, chat_id: str, file_paths: list[str], caption: str | None = None
) -> bool:
    """Upload up to TELEGRAM_MEDIA_GROUP_MAX local videos in one album.

    The caption rides on the first item (Telegram shows it for the album).
    Returns True on success, False on network/API error or an empty list.
    Raises ValueError if token or chat_id format is invalid.
    """
    _check_credentials(bot_token, chat_id)

    paths = file_paths[:TELEGRAM_MEDIA_GROUP_MAX]
    if not paths:
        return False

    media: list[dict[str, Any]] = []
    file_paths: dict[str, str] = {}
    for i, path in enumerate(paths):
        key = f"file{i}"
        item: dict[str, Any] = {"type": "video", "media": f"attach://{key}"}
        if i == 0 and caption:
            item["caption"] = caption[:1024]
        media.append(item)
        file_paths[key] = path

    return _post_files(
        bot_token,
        "sendMediaGroup",
        {"chat_id": chat_id, "media": json.dumps(media)},
        file_paths,
    )


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


def get_chat(bot_token: str, chat_id: str) -> Optional[dict[str, Any]]:
    """Telegram Bot API getChat — resolves a display name for a chat_id the
    bot has any history with. Unlike detect_chats (getUpdates, a rolling
    buffer that only covers recent activity), this works for any chat_id
    the bot was ever added to or messaged, so it's the right call for
    resolving a name behind a chat_id the operator pasted in by hand.

    Returns None on any failure (invalid format, chat not found, network
    error) rather than raising — this is a display nicety the caller falls
    back gracefully from, never a required step.
    """
    if not _BOT_TOKEN_RE.match(bot_token) or not _CHAT_ID_RE.match(chat_id):
        return None

    url = _method_url(bot_token, "getChat")
    try:
        with httpx.Client(follow_redirects=False, timeout=10) as client:
            resp = client.get(url, params={"chat_id": chat_id})
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not data.get("ok"):
        return None

    result = data.get("result")
    if not isinstance(result, dict):
        return None
    name = (
        result.get("title")
        or " ".join(filter(None, [result.get("first_name"), result.get("last_name")]))
        or result.get("username")
    )
    if not name:
        return None
    return {"name": str(name), "type": str(result.get("type") or "")}


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
