"""Rewrite a video title with the configured LLM, degrading to the raw title.

Shared by the scheduler (caption {ai_title} on a pipeline tick) and the API
(the "generate caption" button on a manually downloaded video), so both honour
the same enabled/offline/cloud configuration.
"""
from __future__ import annotations

import itertools
import logging
import unicodedata

from api.services import llm
from api.services.llm_config import (
    get_default_llm_model,
    get_llm_mode,
    get_ollama_config,
    is_llm_enabled,
)

log = logging.getLogger(__name__)


def sanitize_llm_title(value: str) -> str:
    """Strip Unicode control/format chars from LLM output and cap at 500 chars."""
    return "".join(itertools.islice(
        (c for c in value if not unicodedata.category(c).startswith("C")), 500
    ))


def rewrite_with_llm(
    prompt: str,
    title: str,
    description: str,
    channel_description: str = "",
    *,
    transcript: str = "",
) -> str:
    """Return the rewritten title, or `title` on any failure path."""
    if not is_llm_enabled():
        log.warning("LLM globally disabled; using raw title: %s", title)
        return title

    if get_llm_mode() == "offline":
        model, base_url = get_ollama_config()
        if not model or not base_url:
            log.warning("OLLAMA_MODEL/OLLAMA_BASE_URL not set; using raw title")
            return title
        try:
            rewritten = llm.rewrite_title_offline(
                prompt,
                title=title,
                description=description,
                model=model,
                base_url=base_url,
                channel_description=channel_description,
                transcript=transcript,
            )
        except llm.LLMCallFailed as e:
            log.warning("Ollama rewrite skipped: %s", e)
            return title
        return sanitize_llm_title(rewritten) if rewritten else title

    default = get_default_llm_model()
    if default is None:
        log.warning("Caption rewrite requested but no default LLM is set")
        return title
    try:
        rewritten = llm.rewrite_title(
            prompt,
            title=title,
            description=description,
            model_id=default.model_id,
            api_key_encrypted=default.api_key_encrypted,
            api_base=default.api_base,
            api_version=default.api_version,
            channel_description=channel_description,
            transcript=transcript,
        )
    except (llm.LLMNotConfigured, llm.LLMCallFailed) as e:
        log.warning("LLM rewrite skipped: %s", e)
        return title
    return sanitize_llm_title(rewritten) if rewritten else title
