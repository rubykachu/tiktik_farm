"""Read helpers + invariant enforcement for LLM caption rewrite config.

The worker pulls the default LLM here so it never has to import a router.
`set_default_llm_model` is the single transactional path that flips the
`is_default` flag — keeping the "only one row is default" invariant.
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import update
from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import AppSetting, LLMModel


def get_default_llm_model() -> Optional[LLMModel]:
    """Return the row flagged is_default, or None."""
    with Session(_api_db.engine) as s:
        row = s.exec(
            select(LLMModel).where(LLMModel.is_default == True)  # noqa: E712
        ).first()
        if row:
            s.expunge(row)
        return row


def is_llm_enabled() -> bool:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, "llm.enabled")
        return bool(row and row.value.lower() == "true")


def _set_app_setting(key: str, value: str) -> None:
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
            row.updated_at = now_utc()
        s.add(row)
        s.commit()


def set_llm_enabled(enabled: bool) -> None:
    _set_app_setting("llm.enabled", "true" if enabled else "false")


def get_llm_mode() -> str:
    """Return 'cloud' or 'offline'. Defaults to 'cloud' when row absent."""
    with Session(_api_db.engine) as s:
        row = s.get(AppSetting, "llm.mode")
        return row.value if row else "cloud"


def set_llm_mode(mode: str) -> None:
    _set_app_setting("llm.mode", mode)


def get_ollama_config() -> tuple[str, str]:
    """Read env vars. Not DB-backed — changes require restart."""
    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    return model, base_url


def set_default_llm_model(model_id: int) -> None:
    """Atomically set this row's is_default=True and clear all others.

    Wrapped in a single transaction so concurrent writers cannot leave
    zero or two rows flagged default. Raises LookupError if the id does
    not exist.
    """
    with Session(_api_db.engine) as s:
        with s.begin():
            target = s.get(LLMModel, model_id)
            if target is None:
                raise LookupError(f"LLMModel id={model_id} not found")
            s.exec(
                update(LLMModel)
                .where(LLMModel.id != model_id)
                .values(is_default=False, updated_at=now_utc())
            )
            target.is_default = True
            target.updated_at = now_utc()
            s.add(target)
