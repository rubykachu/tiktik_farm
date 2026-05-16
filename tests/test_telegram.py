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

    def test_valid_channel_username(self):
        with patch("api.services.telegram.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post.return_value = mock_resp

            assert send_message(VALID_TOKEN, "@my_channel", "hi") is True


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


class TestNetworkError:
    @patch("api.services.telegram.httpx.Client")
    def test_network_exception_returns_false(self, mock_cls):
        import httpx

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        result = send_message(VALID_TOKEN, VALID_CHAT, "msg")
        assert result is False

    @patch("api.services.telegram.httpx.Client")
    def test_timeout_returns_false(self, mock_cls):
        import httpx

        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ReadTimeout("timed out")

        result = send_message(VALID_TOKEN, VALID_CHAT, "msg")
        assert result is False
