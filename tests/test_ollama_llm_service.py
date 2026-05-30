"""Unit tests for llm.rewrite_title_offline (Task 2).

No real network — litellm.completion is patched throughout.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import call, patch

import pytest


def _mock_completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestRewriteTitleOfflineHappy:
    def test_returns_stripped_content(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("  Catchy Offline Title  \n")
            out = llm.rewrite_title_offline(
                "Rewrite as TikTok title",
                title="orig",
                description="some desc",
                model="llama3.2",
                base_url="http://localhost:11434",
            )
        assert out == "Catchy Offline Title"

    def test_model_prefixed_with_ollama(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                "p", title="t", description="d",
                model="llama3.2", base_url="http://localhost:11434",
            )
        kw = mock.call_args.kwargs
        assert kw["model"] == "ollama/llama3.2"

    def test_api_base_forwarded(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                "p", title="t", description="d",
                model="llama3.2", base_url="http://remote:11434",
            )
        kw = mock.call_args.kwargs
        assert kw["api_base"] == "http://remote:11434"

    def test_no_api_key_in_call(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                "p", title="t", description="d",
                model="llama3.2", base_url="http://localhost:11434",
            )
        kw = mock.call_args.kwargs
        assert "api_key" not in kw

    def test_max_tokens_and_timeout_same_as_cloud(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                "p", title="t", description="d",
                model="llama3.2", base_url="http://localhost:11434",
            )
        kw = mock.call_args.kwargs
        assert kw["max_tokens"] == llm.MAX_OUTPUT_TOKENS
        assert kw["timeout"] == llm.DEFAULT_TIMEOUT

    def test_truncates_long_description(self):
        from api.services import llm

        long_desc = "x" * 5000
        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                "p", title="t", description=long_desc,
                model="llama3.2", base_url="http://localhost:11434",
            )
        user_msg = mock.call_args.kwargs["messages"][1]["content"]
        assert "x" * (llm.DESCRIPTION_MAX_CHARS + 1) not in user_msg

    def test_empty_response_returns_empty_string(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("")
            out = llm.rewrite_title_offline(
                "p", title="t", description="d",
                model="llama3.2", base_url="http://localhost:11434",
            )
        assert out == ""


class TestRewriteTitleOfflineChannelDescription:
    def test_channel_description_injected_into_system_message(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                "base prompt",
                title="t",
                description="d",
                model="llama3.2",
                base_url="http://localhost:11434",
                channel_description="food review channel",
            )
        system_msg = mock.call_args.kwargs["messages"][0]["content"]
        assert "Channel description:" in system_msg
        assert "food review channel" in system_msg

    def test_empty_channel_description_keeps_system_message_unchanged(self):
        from api.services import llm

        prompt = "base prompt"
        with patch.object(llm.litellm, "completion") as mock:
            mock.return_value = _mock_completion("ok")
            llm.rewrite_title_offline(
                prompt,
                title="t",
                description="d",
                model="llama3.2",
                base_url="http://localhost:11434",
            )
        system_msg = mock.call_args.kwargs["messages"][0]["content"]
        assert system_msg == prompt


class TestRewriteTitleOfflineErrors:
    def test_litellm_exception_wrapped_as_llm_call_failed(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion", side_effect=RuntimeError("conn refused")):
            with pytest.raises(llm.LLMCallFailed) as exc:
                llm.rewrite_title_offline(
                    "p", title="t", description="d",
                    model="llama3.2", base_url="http://localhost:11434",
                )
        assert "conn refused" in str(exc.value)

    def test_timeout_exception_wrapped(self):
        from api.services import llm

        with patch.object(llm.litellm, "completion", side_effect=TimeoutError("timed out")):
            with pytest.raises(llm.LLMCallFailed):
                llm.rewrite_title_offline(
                    "p", title="t", description="d",
                    model="llama3.2", base_url="http://localhost:11434",
                )
