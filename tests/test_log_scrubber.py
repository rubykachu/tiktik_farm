"""Unit tests for api.logging.BotTokenScrubFilter.

Telegram bot tokens have the shape `123456:ABCDEFghij...` and must never
appear in logs. The filter redacts them to `bot***`.
"""
from __future__ import annotations

import logging

from api.logging import BotTokenScrubFilter, scrub_bot_tokens


class TestScrubFunction:
    def test_redacts_token_in_url(self):
        text = "POST https://api.telegram.org/bot123456:ABCDEFghij_kLmNoPqRsTuVwXyZ0123/sendMessage"
        out = scrub_bot_tokens(text)
        assert "123456:ABCDEFghij" not in out
        assert "bot***" in out
        assert "/sendMessage" in out

    def test_redacts_standalone_token(self):
        text = "token=bot987654321:XYZ_abc-DEF_ghi-jklmnopqrstuvwxyz"
        out = scrub_bot_tokens(text)
        assert "987654321" not in out
        assert "bot***" in out

    def test_preserves_unrelated_text(self):
        text = "normal log message, no secrets here"
        assert scrub_bot_tokens(text) == text

    def test_redacts_multiple_tokens(self):
        text = "first bot111:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa second bot222:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        out = scrub_bot_tokens(text)
        assert "aaaaa" not in out
        assert "bbbbb" not in out
        assert out.count("bot***") == 2

    def test_redacts_bytes_literal_with_opaque_token(self):
        # Reproduces the Azure leak: httpx surfaces the header value verbatim
        # when the underlying request is rejected for "Illegal header value".
        text = (
            "litellm.APIError: AzureException APIError - Illegal header value "
            "b'3UFQDUs5PEXo6GeWAD1o0ZzDj7QUDuOTE0ys0e1R2XTfNNydEKncJQQJ99CEACqBBLyXJ3w3AAAAACOGRG3H'"
        )
        out = scrub_bot_tokens(text)
        assert "3UFQDUs5PEXo6GeWAD" not in out
        assert "RG3H" not in out
        assert "b'***'" in out

    def test_redacts_long_opaque_alphanumeric_token(self):
        text = "401 error using key 3UFQDUs5PEXo6GeWAD1o0ZzDj7QUDuOTE0ys0e1R2XTfNNydEKncJQQJ in body"
        out = scrub_bot_tokens(text)
        assert "3UFQDUs5PEXo6GeWAD" not in out
        assert "***" in out


class TestFilterIntegration:
    def test_filter_redacts_log_record_message(self):
        logger = logging.getLogger("test_scrub_msg")
        logger.handlers = []
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        captured: list[str] = []
        handler.emit = lambda record: captured.append(handler.format(record))
        handler.addFilter(BotTokenScrubFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        logger.info("Calling https://api.telegram.org/bot555:abcdefghij1234567890ABCDEFGHIJKL/sendMessage")
        assert len(captured) == 1
        assert "555:abcdefghij" not in captured[0]
        assert "bot***" in captured[0]

    def test_filter_redacts_args(self):
        logger = logging.getLogger("test_scrub_args")
        logger.handlers = []
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        captured: list[str] = []
        handler.emit = lambda record: captured.append(handler.format(record))
        handler.addFilter(BotTokenScrubFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        logger.info("token=%s", "bot777:ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ")
        assert "777:ZZZZZZ" not in captured[0]
        assert "bot***" in captured[0]
