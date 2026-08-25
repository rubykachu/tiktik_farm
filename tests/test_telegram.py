"""Tests for api/services/telegram.py — mocked httpx."""
import pytest
from unittest.mock import patch, MagicMock

from api.services.telegram import send_message


VALID_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678"
VALID_CHAT = "-1001234567890"


class TestValidation:
    def test_invalid_token_raises(self):
        with pytest.raises(ValueError, match="bot_token format"):
            send_message("bad-token", VALID_CHAT, "hello")

    def test_invalid_chat_id_raises(self):
        with pytest.raises(ValueError, match="chat_id format"):
            send_message(VALID_TOKEN, "not valid!", "hello")

    def test_short_token_raises(self):
        with pytest.raises(ValueError, match="bot_token format"):
            send_message("123:short", VALID_CHAT, "hello")

    def test_username_chat_id_raises(self):
        with pytest.raises(ValueError, match="chat_id format"):
            send_message(VALID_TOKEN, "@my_channel", "hi")


class TestHappyPath:
    @patch("api.services.telegram.httpx.Client")
    def test_success_200(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp

        result = send_message(VALID_TOKEN, VALID_CHAT, "Test message")
        assert result is True

        call_args = mock_client.post.call_args
        assert "sendMessage" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["chat_id"] == VALID_CHAT
        assert payload["text"] == "Test message"

    @patch("api.services.telegram.httpx.Client")
    def test_non_200_returns_false(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_client.post.return_value = mock_resp

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is False


class TestNoRedirectFollow:
    @patch("api.services.telegram.httpx.Client")
    def test_follow_redirects_false(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.text = "Redirecting..."
        mock_client.post.return_value = mock_resp

        result = send_message(VALID_TOKEN, VALID_CHAT, "msg")
        assert result is False

        mock_cls.assert_called_once_with(follow_redirects=False, timeout=10)


def _mock_post(mock_cls):
    """Wire a patched httpx.Client and hand back its .post mock."""
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_client.post


def _response(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "body"
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


class TestNetworkError:
    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_network_exception_returns_false(self, mock_cls, _sleep):
        import httpx

        _mock_post(mock_cls).side_effect = httpx.ConnectError("connection refused")

        result = send_message(VALID_TOKEN, VALID_CHAT, "msg")
        assert result is False

    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_timeout_returns_false(self, mock_cls, _sleep):
        import httpx

        _mock_post(mock_cls).side_effect = httpx.ReadTimeout("timed out")

        result = send_message(VALID_TOKEN, VALID_CHAT, "msg")
        assert result is False


class TestRetry:
    """A downloaded video is deleted right after notifying, so a transient
    failure has to be retried before the send is written off."""

    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_retries_until_success(self, mock_cls, mock_sleep):
        import httpx

        post = _mock_post(mock_cls)
        post.side_effect = [
            httpx.ConnectError("down"),
            _response(500),
            _response(200),
        ]

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is True
        assert post.call_count == 3
        assert [c.args[0] for c in mock_sleep.call_args_list] == [1.0, 2.0]

    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_gives_up_after_max_attempts(self, mock_cls, mock_sleep):
        from api.services.telegram import MAX_SEND_ATTEMPTS

        _mock_post(mock_cls).return_value = _response(503)

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is False
        assert mock_cls.return_value.__enter__.return_value.post.call_count == MAX_SEND_ATTEMPTS
        # Backoff doubles and there is no sleep after the final attempt.
        assert [c.args[0] for c in mock_sleep.call_args_list] == [1.0, 2.0, 4.0, 8.0]

    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_client_error_is_not_retried(self, mock_cls, mock_sleep):
        post = _mock_post(mock_cls)
        post.return_value = _response(400)

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is False
        assert post.call_count == 1
        mock_sleep.assert_not_called()

    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_429_honours_retry_after(self, mock_cls, mock_sleep):
        post = _mock_post(mock_cls)
        post.side_effect = [
            _response(429, {"parameters": {"retry_after": 7}}),
            _response(200),
        ]

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is True
        assert mock_sleep.call_args_list[0].args[0] == 7.0

    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_429_retry_after_is_capped(self, mock_cls, mock_sleep):
        from api.services.telegram import MAX_RETRY_AFTER_SECONDS

        post = _mock_post(mock_cls)
        post.side_effect = [
            _response(429, {"parameters": {"retry_after": 99999}}),
            _response(200),
        ]

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is True
        assert mock_sleep.call_args_list[0].args[0] == MAX_RETRY_AFTER_SECONDS

    @patch("api.services.telegram.time.monotonic")
    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_deadline_stops_retrying(self, mock_cls, mock_sleep, mock_monotonic):
        from api.services.telegram import RETRY_DEADLINE_SECONDS

        post = _mock_post(mock_cls)
        post.return_value = _response(500)
        # Second reading is past the deadline, so no retry is scheduled.
        mock_monotonic.side_effect = [0.0, RETRY_DEADLINE_SECONDS + 1]

        assert send_message(VALID_TOKEN, VALID_CHAT, "msg") is False
        assert post.call_count == 1
        mock_sleep.assert_not_called()


class TestUploadRetry:
    @patch("api.services.telegram.time.sleep")
    @patch("api.services.telegram.httpx.Client")
    def test_file_handles_reopened_per_attempt(self, mock_cls, _sleep, tmp_path):
        from api.services.telegram import send_document

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"data")

        post = _mock_post(mock_cls)
        sent_bytes = []

        def _capture(url, data=None, files=None):
            sent_bytes.append(files["document"][1].read())
            return _response(500) if len(sent_bytes) == 1 else _response(200)

        post.side_effect = _capture

        assert send_document(VALID_TOKEN, VALID_CHAT, str(video)) is True
        # A reused handle would send b"" on the retry.
        assert sent_bytes == [b"data", b"data"]


class TestDetectChatsHelpers:
    @patch("api.services.telegram.httpx.Client")
    def test_detect_chats_uses_no_redirect_client(self, mock_cls):
        from api.services.telegram import detect_chats

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        chat_update = {
            "message": {"chat": {"id": -50, "title": "G", "type": "supergroup"}}
        }

        def post_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": [chat_update]}
            return mock_resp

        mock_client.post.side_effect = post_side_effect
        result = detect_chats(VALID_TOKEN)
        mock_cls.assert_called_with(follow_redirects=False, timeout=15)
        assert mock_client.post.call_count == 1
        assert "getUpdates" in str(mock_client.post.call_args[0][0])
        assert len(result) == 1

    def test_chats_from_updates_includes_private_and_supergroup(self):
        from api.services.telegram import _chats_from_updates_result

        rows = _chats_from_updates_result(
            [
                {"message": {"chat": {"id": 1, "type": "private", "first_name": "Alice"}}},
                {
                    "my_chat_member": {
                        "chat": {"id": -50, "title": "G", "type": "supergroup"}
                    }
                },
            ]
        )
        assert len(rows) == 2
        assert rows[0] == {"id": 1, "title": "Alice", "type": "private"}
        assert rows[1] == {"id": -50, "title": "G", "type": "supergroup"}

    def test_chats_from_updates_private_uses_first_name(self):
        from api.services.telegram import _chats_from_updates_result

        rows = _chats_from_updates_result(
            [{"message": {"chat": {"id": 99, "type": "private", "first_name": "Bob"}}}]
        )
        assert rows[0]["title"] == "Bob"

    @patch("api.services.telegram.httpx.Client")
    def test_detect_chats_raises_when_no_recent_messages(self, mock_cls):
        from api.services.telegram import detect_chats, TelegramApiError

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        def get_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": {"url": ""}}
            return mock_resp

        def post_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": []}
            return mock_resp

        mock_client.post.side_effect = post_side_effect
        mock_client.get.side_effect = get_side_effect

        with pytest.raises(TelegramApiError, match="Không tìm thấy"):
            detect_chats(VALID_TOKEN)

    @patch("api.services.telegram.httpx.Client")
    def test_detect_chats_raises_when_webhook_active(self, mock_cls):
        from api.services.telegram import detect_chats, TelegramApiError

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        def post_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "result": []}
            return mock_resp

        def get_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "ok": True, "result": {"url": "https://example.com/hook"}
            }
            return mock_resp

        mock_client.post.side_effect = post_side_effect
        mock_client.get.side_effect = get_side_effect

        with pytest.raises(TelegramApiError, match="webhook"):
            detect_chats(VALID_TOKEN)

    def test_chats_from_updates_channel_post_key(self):
        """H2: channel_post update type returns channel chat."""
        from api.services.telegram import _chats_from_updates_result

        rows = _chats_from_updates_result(
            [{"channel_post": {"chat": {"id": -100, "title": "MyChannel", "type": "channel"}}}]
        )
        assert len(rows) == 1
        assert rows[0] == {"id": -100, "title": "MyChannel", "type": "channel"}

    def test_chats_from_updates_deduplicates_same_chat_id(self):
        """E1: duplicate chat.id across multiple updates → only first entry kept."""
        from api.services.telegram import _chats_from_updates_result

        rows = _chats_from_updates_result(
            [
                {"message": {"chat": {"id": -50, "title": "First", "type": "supergroup"}}},
                {"message": {"chat": {"id": -50, "title": "Second", "type": "supergroup"}}},
            ]
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "First"

    def test_chats_from_updates_non_numeric_id_skipped(self):
        """E2: non-numeric chat.id is skipped without raising."""
        from api.services.telegram import _chats_from_updates_result

        rows = _chats_from_updates_result(
            [{"message": {"chat": {"id": "not-a-number", "title": "Bad", "type": "supergroup"}}}]
        )
        assert rows == []

    def test_chats_from_updates_title_fallback_to_chat_id_string(self):
        """E3: all title fields absent → title falls back to str(chat_id)."""
        from api.services.telegram import _chats_from_updates_result

        rows = _chats_from_updates_result(
            [{"message": {"chat": {"id": -999, "type": "supergroup"}}}]
        )
        assert rows[0]["title"] == "-999"

    @patch("api.services.telegram.httpx.Client")
    def test_detect_chats_returned_dict_shape(self, mock_cls):
        """H3: returned items have id (int), title (str), type (str) keys."""
        from api.services.telegram import detect_chats

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": -42, "title": "Grp", "type": "supergroup"}}}
            ],
        }
        mock_client.post.return_value = mock_resp

        result = detect_chats(VALID_TOKEN)
        assert len(result) == 1
        item = result[0]
        assert isinstance(item["id"], int)
        assert isinstance(item["title"], str)
        assert isinstance(item["type"], str)
        assert item == {"id": -42, "title": "Grp", "type": "supergroup"}

    @patch("api.services.telegram.httpx.Client")
    def test_detect_chats_raises_on_invalid_json(self, mock_cls):
        """P2: malformed JSON body from getUpdates raises TelegramApiError."""
        from api.services.telegram import detect_chats, TelegramApiError

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("no JSON")
        mock_client.post.return_value = mock_resp

        with pytest.raises(TelegramApiError, match="invalid response"):
            detect_chats(VALID_TOKEN)

    @patch("api.services.telegram.httpx.Client")
    def test_detect_chats_raises_when_ok_false(self, mock_cls):
        """P3: ok=false in getUpdates response raises TelegramApiError."""
        from api.services.telegram import detect_chats, TelegramApiError

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "description": "Unauthorized"}
        mock_client.post.return_value = mock_resp

        with pytest.raises(TelegramApiError, match="API error"):
            detect_chats(VALID_TOKEN)


class TestGetChat:
    def test_invalid_token_returns_none(self):
        from api.services.telegram import get_chat

        assert get_chat("bad-token", VALID_CHAT) is None

    def test_invalid_chat_id_returns_none(self):
        from api.services.telegram import get_chat

        assert get_chat(VALID_TOKEN, "@my_channel") is None

    @patch("api.services.telegram.httpx.Client")
    def test_group_uses_title(self, mock_cls):
        from api.services.telegram import get_chat

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": -1001234567890, "type": "supergroup", "title": "Kênh vận hành"},
        }
        mock_client.get.return_value = mock_resp

        result = get_chat(VALID_TOKEN, VALID_CHAT)
        assert result == {"name": "Kênh vận hành", "type": "supergroup"}
        call_args = mock_client.get.call_args
        assert "getChat" in call_args[0][0]
        assert call_args[1]["params"]["chat_id"] == VALID_CHAT

    @patch("api.services.telegram.httpx.Client")
    def test_private_chat_falls_back_to_first_last_name(self, mock_cls):
        from api.services.telegram import get_chat

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 123, "type": "private", "first_name": "An", "last_name": "Nguyen"},
        }
        mock_client.get.return_value = mock_resp

        assert get_chat(VALID_TOKEN, "123") == {"name": "An Nguyen", "type": "private"}

    @patch("api.services.telegram.httpx.Client")
    def test_private_chat_falls_back_to_username_without_name(self, mock_cls):
        from api.services.telegram import get_chat

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"id": 123, "type": "private", "username": "an_nguyen"},
        }
        mock_client.get.return_value = mock_resp

        assert get_chat(VALID_TOKEN, "123") == {"name": "an_nguyen", "type": "private"}

    @patch("api.services.telegram.httpx.Client")
    def test_ok_false_returns_none(self, mock_cls):
        from api.services.telegram import get_chat

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "description": "chat not found"}
        mock_client.get.return_value = mock_resp

        assert get_chat(VALID_TOKEN, VALID_CHAT) is None

    @patch("api.services.telegram.httpx.Client")
    def test_non_200_returns_none(self, mock_cls):
        from api.services.telegram import get_chat

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client.get.return_value = mock_resp

        assert get_chat(VALID_TOKEN, VALID_CHAT) is None

    @patch("api.services.telegram.httpx.Client")
    def test_network_error_returns_none(self, mock_cls):
        import httpx
        from api.services.telegram import get_chat

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("boom")

        assert get_chat(VALID_TOKEN, VALID_CHAT) is None
