"""LLM caption rewrite via litellm. Single sync call per (pipeline, video).

The worker caches results per `video_id` within a tick so multi-account
pipelines only pay for one rewrite. Failures degrade silently to the raw
YouTube title — the pipeline run is never aborted because of an LLM hiccup.
"""
from __future__ import annotations

import logging

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
DEFAULT_TIMEOUT = 30          # seconds; bigger blows up tick latency
MAX_OUTPUT_TOKENS = 200       # ~one TikTok caption line


class LLMNotConfigured(Exception):
    """The encrypted key is missing or unreadable."""


class LLMCallFailed(Exception):
    """litellm.completion raised."""


def _decrypt_required(api_key_encrypted: str) -> str:
    plain = decrypt(api_key_encrypted)
    if not plain:
        raise LLMNotConfigured("LLM API key missing or unreadable")
    return plain


def rewrite_title(
    prompt: str,
    title: str,
    description: str,
    *,
    model_id: str,
    api_key_encrypted: str,
) -> str:
    api_key = _decrypt_required(api_key_encrypted)
    user_msg = (
        f"Title: {title}\n"
        f"Description: {truncate(description, DESCRIPTION_MAX_CHARS, suffix='')}"
    )
    try:
        response = litellm.completion(
            model=model_id,
            api_key=api_key,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout=DEFAULT_TIMEOUT,
        )
    except Exception as e:
        log.warning("LLM rewrite failed for model %s: %s", model_id, e)
        raise LLMCallFailed(str(e)) from e

    content = (response.choices[0].message.content or "").strip()
    return content


_TEST_ERROR_MAX_CHARS = 200


def test_completion(*, model_id: str, api_key_encrypted: str) -> tuple[bool, str]:
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
        )
    except Exception as e:
        safe = scrub_bot_tokens(str(e))[:_TEST_ERROR_MAX_CHARS]
        return (False, safe or "error")
    return (True, "ok")
