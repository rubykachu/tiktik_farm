"""CRUD for user-managed LLM endpoints. Stored API keys are Fernet
ciphertext; the masked form is returned on read."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import LLMModel
from api.schemas import LLMModelCreate, LLMModelRead, LLMModelUpdate
from api.services import secrets
from api.services.llm_config import set_default_llm_model

router = APIRouter(prefix="/api/llm-models", tags=["llm-models"])


def _to_read(row: LLMModel) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "model_id": row.model_id,
        "api_key_masked": secrets.mask(secrets.decrypt(row.api_key_encrypted)),
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
        is_default=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    if payload.set_default:
        set_default_llm_model(row.id)  # type: ignore[arg-type]
        session.expire_all()
        row = session.get(LLMModel, row.id)  # type: ignore[assignment]

    return _to_read(row)  # type: ignore[arg-type]


@router.patch("/{model_id}", response_model=LLMModelRead)
def update_model(
    model_id: int,
    payload: LLMModelUpdate,
    session: Session = Depends(get_session),
):
    row = session.get(LLMModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="llm model not found")

    changed = False
    if payload.label is not None:
        row.label = payload.label
        changed = True
    if payload.model_id is not None:
        row.model_id = payload.model_id
        changed = True
    # Empty / absent api_key → keep existing.
    if payload.api_key:
        row.api_key_encrypted = secrets.encrypt(payload.api_key)
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
    session.expire_all()
    row = session.get(LLMModel, model_id)
    return _to_read(row)  # type: ignore[arg-type]


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: int, session: Session = Depends(get_session)):
    row = session.get(LLMModel, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="llm model not found")
    session.delete(row)
    session.commit()
