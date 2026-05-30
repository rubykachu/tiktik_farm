"""Unit tests for api/services/llm.py — wraps litellm with secrets-aware
key handling, truncation, and exception normalization. No real network."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(tmp_path / "k.bin"))
    from api.services import secrets as _secrets
    _secrets._reset_for_tests()
    yield
    _secrets._reset_for_tests()


def _mock_completion(content: str):
    """Return a litellm-style response object exposing
    `.choices[0].message.content`."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestRewriteTitleHappy:
    def test_returns_stripped_content(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("  Rewritten Title  \n")
            out = llm.rewrite_title(
                "Rewrite as clickbait",
                title="orig",
                description="some desc",
                model_id="gemini/gemini-2.0-flash-lite",
                api_key_encrypted=encrypt("sk-test-1234"),
            )
        assert out == "Rewritten Title"

    def test_passes_decrypted_api_key_to_litellm(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title(
                "prompt",
                title="t",
                description="d",
                model_id="gemini/x",
                api_key_encrypted=encrypt("REAL-KEY-XYZ"),
            )
        call_kwargs = mock.call_args.kwargs
        assert call_kwargs["api_key"] == "REAL-KEY-XYZ"
        assert call_kwargs["model"] == "gemini/x"

    def test_truncates_long_description(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        long_desc = "x" * 5000
        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title(
                "p",
                title="t",
                description=long_desc,
                model_id="gemini/x",
                api_key_encrypted=encrypt("k"),
            )
        user_msg = mock.call_args.kwargs["messages"][1]["content"]
        # The user message contains "Title: ... Description: <truncated>".
        assert "x" * (llm.DESCRIPTION_MAX_CHARS + 1) not in user_msg

    def test_max_tokens_and_timeout_pinned(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title(
                "p", title="t", description="d",
                model_id="gemini/x", api_key_encrypted=encrypt("k"),
            )
        kw = mock.call_args.kwargs
        assert kw["max_tokens"] == llm.MAX_OUTPUT_TOKENS
        assert kw["timeout"] == llm.DEFAULT_TIMEOUT


class TestRewriteTitleErrors:
    def test_unreadable_key_raises_llm_not_configured(self, isolated_secret):
        from api.services import llm
        # Bad ciphertext — decrypt returns "" → LLMNotConfigured.
        with pytest.raises(llm.LLMNotConfigured):
            llm.rewrite_title(
                "p", title="t", description="d",
                model_id="gemini/x",
                api_key_encrypted="this-is-not-fernet",
            )

    def test_empty_encrypted_key_raises_llm_not_configured(self, isolated_secret):
        from api.services import llm
        with pytest.raises(llm.LLMNotConfigured):
            llm.rewrite_title(
                "p", title="t", description="d",
                model_id="gemini/x", api_key_encrypted="",
            )

    def test_litellm_exception_wrapped_as_llm_call_failed(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion", side_effect=RuntimeError("rate limit")):
            with pytest.raises(llm.LLMCallFailed) as exc:
                llm.rewrite_title(
                    "p", title="t", description="d",
                    model_id="gemini/x", api_key_encrypted=encrypt("k"),
                )
        assert "rate limit" in str(exc.value)


class TestBuildSystemPrompt:
    def test_empty_channel_description_returns_prompt_unchanged(self):
        from api.services.llm import _build_system_prompt
        prompt = "You are a TikTok caption writer."
        assert _build_system_prompt(prompt, "") == prompt
        assert _build_system_prompt(prompt) == prompt

    def test_whitespace_only_channel_description_treated_as_empty(self):
        from api.services.llm import _build_system_prompt
        prompt = "base prompt"
        assert _build_system_prompt(prompt, "   ") == prompt

    def test_non_empty_channel_description_appended_to_prompt(self):
        from api.services.llm import _build_system_prompt
        out = _build_system_prompt("base", "kênh review xe điện")
        assert "base" in out
        assert "Channel description:" in out
        assert "kênh review xe điện" in out

    def test_long_channel_description_truncated(self):
        from api.services.llm import _build_system_prompt, CHANNEL_DESC_INJECT_MAX
        long_desc = "a" * (CHANNEL_DESC_INJECT_MAX + 500)
        out = _build_system_prompt("base", long_desc)
        assert "a" * (CHANNEL_DESC_INJECT_MAX + 1) not in out

    def test_exact_boundary_not_truncated(self):
        from api.services.llm import _build_system_prompt, CHANNEL_DESC_INJECT_MAX
        exact = "a" * CHANNEL_DESC_INJECT_MAX
        out = _build_system_prompt("base", exact)
        assert exact in out

    def test_label_is_channel_description(self):
        from api.services.llm import _build_system_prompt
        out = _build_system_prompt("base", "food channel")
        assert "Channel description:" in out

    def test_newlines_normalized_to_spaces(self):
        from api.services.llm import _build_system_prompt
        out = _build_system_prompt("base", "line1\nline2\r\ntabs\there")
        assert "\n" not in out.split("Channel description:")[1]
        assert "line1 line2" in out


class TestRewriteTitleWithChannelDescription:
    def test_channel_description_injected_into_system_message(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title(
                "base prompt",
                title="t",
                description="d",
                model_id="gemini/x",
                api_key_encrypted=encrypt("k"),
                channel_description="channel about food",
            )
        system_msg = mock.call_args.kwargs["messages"][0]["content"]
        assert "Channel description:" in system_msg
        assert "channel about food" in system_msg

    def test_empty_channel_description_keeps_system_message_unchanged(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        prompt = "base prompt"
        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title(
                prompt,
                title="t",
                description="d",
                model_id="gemini/x",
                api_key_encrypted=encrypt("k"),
            )
        system_msg = mock.call_args.kwargs["messages"][0]["content"]
        assert system_msg == prompt


class TestTestCompletion:
    def test_returns_ok_on_success(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("pong")
            ok, msg = llm.test_completion(
                model_id="gemini/x", api_key_encrypted=encrypt("k"),
            )
        assert ok is True
        assert "ok" in msg.lower() or msg == "ok"

    def test_returns_failure_message_on_exception(self, isolated_secret):
        from api.services import llm
        from api.services.secrets import encrypt

        with patch.object(llm.litellm, "completion", side_effect=ValueError("bad key")):
            ok, msg = llm.test_completion(
                model_id="gemini/x", api_key_encrypted=encrypt("k"),
            )
        assert ok is False
        assert "bad key" in msg

    def test_returns_failure_when_key_missing(self, isolated_secret):
        from api.services import llm
        ok, msg = llm.test_completion(
            model_id="gemini/x", api_key_encrypted="",
        )
        assert ok is False
