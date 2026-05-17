"""CRUD for user-managed LLM endpoints. Stored API keys are Fernet
ciphertext; the masked form is returned on read."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import LLMModel
from api.schemas import (
    LLMModelCreate,
    LLMModelRead,
    LLMModelUpdate,
    _require_azure_fields,
)
from api.services import secrets
from api.services.llm_config import set_default_llm_model

router = APIRouter(prefix="/api/llm-models", tags=["llm-models"])


def _to_read(row: LLMModel) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "model_id": row.model_id,
        "api_key_masked": secrets.mask(secrets.decrypt(row.api_key_encrypted)),
        "api_base": row.api_base,
        "api_version": row.api_version,
        "is_default": row.is_default,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("", response_model=List[LLMModelRead])
def list_models(session: Session = Depends(get_session)):
    rows = session.exec(select(LLMModel).order_by(LLMModel.created_at)).all()
    return [_to_read(r) for r in rows]


@router.post("", response_model=LLMModelRead, status_code=status.HTTP_201_CREATED)
def create_model(payload: LLMModelCreate, session: Session = Depends(get_session)):
    row = LLMModel(
        label=payload.label,
        model_id=payload.model_id,
        api_key_encrypted=secrets.encrypt(payload.api_key),
        api_base=payload.api_base or None,
        api_version=payload.api_version or None,
        is_default=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    if payload.set_default:
        set_default_llm_model(row.id)  # type: ignore[arg-type]
        session.refresh(row)

    return _to_read(row)


def _resolve_optional(payload_val, current):
    """Merge a PATCH field over a stored value. None = unchanged, "" = clear."""
    if payload_val is None:
        return current
    return payload_val or None


@router.patch("/{model_id}", response_model=LLMModelRead)
def update_model(
    model_id: int,
    payload: LLMModelUpdate,
    session: Session = Depends(get_session),
):
    row = session.get(LLMModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="llm model not found")

    # Validate the post-merge candidate against schema invariants BEFORE
    # mutating row state, so a 422 leaves the stored row untouched.
    candidate_model_id = payload.model_id if payload.model_id is not None else row.model_id
    if payload.clear_api_base:
        candidate_base = None
        candidate_version = None
    else:
        candidate_base = _resolve_optional(payload.api_base, row.api_base)
        candidate_version = _resolve_optional(payload.api_version, row.api_version)
    try:
        _require_azure_fields(candidate_model_id, candidate_base, candidate_version)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    changed = False
    if payload.label is not None:
        row.label = payload.label
        changed = True
    if payload.model_id is not None:
        row.model_id = payload.model_id
        changed = True
    if payload.api_key:
        row.api_key_encrypted = secrets.encrypt(payload.api_key)
        changed = True
    if payload.clear_api_base:
        row.api_base = None
        row.api_version = None
        changed = True
    else:
        if payload.api_base is not None:
            row.api_base = payload.api_base or None
            changed = True
        if payload.api_version is not None:
            row.api_version = payload.api_version or None
            changed = True

    if changed:
        row.updated_at = now_utc()
        session.add(row)
        session.commit()
        session.refresh(row)
    return _to_read(row)


@router.post("/{model_id}/set-default", response_model=LLMModelRead)
def set_default(model_id: int, session: Session = Depends(get_session)):
    try:
        set_default_llm_model(model_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="llm model not found")
    row = session.get(LLMModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="llm model not found")
    session.refresh(row)
    return _to_read(row)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: int, session: Session = Depends(get_session)):
    row = session.get(LLMModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="llm model not found")
    session.delete(row)
    session.commit()
