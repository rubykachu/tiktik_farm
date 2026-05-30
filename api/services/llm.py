"""LLM caption rewrite via litellm. Single sync call per (pipeline, video).

The worker caches results per `video_id` within a tick so multi-account
pipelines only pay for one rewrite. Failures degrade silently to the raw
YouTube title — the pipeline run is never aborted because of an LLM hiccup.
"""
from __future__ import annotations

import logging

# Silence litellm's import-time warnings about missing botocore (Bedrock /
# SageMaker stream decoders). We don't use AWS providers, so the optional
# dep is intentionally absent.
logging.getLogger("LiteLLM").setLevel(logging.ERROR)

import litellm

from api.logging import scrub_bot_tokens
from api.services.secrets import decrypt
from api.utils.text import truncate

log = logging.getLogger(__name__)

# Local-first product — don't ship usage telemetry, callbacks, or debug
# strings to litellm's hosted endpoints.
litellm.telemetry = False
litellm.drop_params = True
litellm.suppress_debug_info = True
litellm.success_callback = []
litellm.failure_callback = []

DESCRIPTION_MAX_CHARS = 2000  # ~500 input tokens → cheap on Gemini Flash
CHANNEL_DESC_MAX_CHARS = 1000  # cap channel_description injection to protect token budget
DEFAULT_TIMEOUT = 60          # seconds; longer captions + local Ollama need headroom
MAX_OUTPUT_TOKENS = 1200      # ~4000 chars, the TikTok caption limit


class LLMNotConfigured(Exception):
    """The encrypted key is missing or unreadable."""


class LLMCallFailed(Exception):
    """litellm.completion raised."""


def _decrypt_required(api_key_encrypted: str) -> str:
    plain = decrypt(api_key_encrypted)
    if not plain:
        raise LLMNotConfigured("LLM API key missing or unreadable")
    return plain


def _build_system_prompt(prompt: str, channel_description: str = "") -> str:
    """Append channel context block to system prompt when provided."""
    cd = truncate(channel_description.strip(), CHANNEL_DESC_MAX_CHARS, suffix="")
    if not cd:
        return prompt
    return (
        f"{prompt}\n\n"
        f"Description channel: {cd}"
    )


def _extra_kwargs(
    api_base: str | None,
    api_version: str | None,
) -> dict:
    """Build optional kwargs for litellm.completion()."""
    kw: dict = {}
    if api_base:
        kw["api_base"] = api_base
    if api_version:
        kw["api_version"] = api_version
    return kw


def rewrite_title(
    prompt: str,
    title: str,
    description: str,
    *,
    model_id: str,
    api_key_encrypted: str,
    api_base: str | None = None,
    api_version: str | None = None,
    channel_description: str = "",
) -> str:
    api_key = _decrypt_required(api_key_encrypted)
    system_msg = _build_system_prompt(prompt, channel_description)
    user_msg = (
        f"Title: {title}\n"
        f"Description: {truncate(description, DESCRIPTION_MAX_CHARS, suffix='')}"
    )
    try:
        response = litellm.completion(
            model=model_id,
            api_key=api_key,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout=DEFAULT_TIMEOUT,
            **_extra_kwargs(api_base, api_version),
        )
    except Exception as e:
        log.warning("LLM rewrite failed for model %s: %s", model_id, e)
        raise LLMCallFailed(str(e)) from e

    content = (response.choices[0].message.content or "").strip()
    return content


def rewrite_title_offline(
    prompt: str,
    title: str,
    description: str,
    *,
    model: str,
    base_url: str,
    channel_description: str = "",
) -> str:
    """Call a local Ollama instance. No API key required."""
    system_msg = _build_system_prompt(prompt, channel_description)
    user_msg = (
        f"Title: {title}\n"
        f"Description: {truncate(description, DESCRIPTION_MAX_CHARS, suffix='')}"
    )
    try:
        response = litellm.completion(
            model=f"ollama/{model}",
            api_base=base_url,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as e:
        safe_err = scrub_bot_tokens(str(e))[:_TEST_ERROR_MAX_CHARS]
        log.warning("Ollama rewrite failed (model=%s base=%s): %s", model, base_url, safe_err)
        raise LLMCallFailed(str(e)) from e
    return (response.choices[0].message.content or "").strip()


_TEST_ERROR_MAX_CHARS = 200


def test_completion(
    *,
    model_id: str,
    api_key_encrypted: str,
    api_base: str | None = None,
    api_version: str | None = None,
) -> tuple[bool, str]:
    """Cheap connectivity probe used by Settings → Test connection.

    The returned message is scrubbed and capped — provider error bodies
    sometimes echo the request including the Authorization header.
    """
    try:
        api_key = _decrypt_required(api_key_encrypted)
    except LLMNotConfigured as e:
        return (False, str(e))
    try:
        litellm.completion(
            model=model_id,
            api_key=api_key,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=DEFAULT_TIMEOUT,
            **_extra_kwargs(api_base, api_version),
        )
    except Exception as e:
        safe = scrub_bot_tokens(str(e))[:_TEST_ERROR_MAX_CHARS]
        return (False, safe or "error")
    return (True, "ok")
