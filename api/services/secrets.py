"""Symmetric encryption for at-rest secrets (LLM API keys).

The key file lives next to the SQLite DB (`data/.secret_key`, mode 600) so
backups stay consistent. Losing the file makes existing ciphertexts
unreadable; `decrypt()` returns "" in that case and the operator must
re-enter API keys from the Settings UI.

The Fernet instance is cached at module load. `_reset_for_tests()` drops it
so test cases can swap the key file via the TIKTOK_SECRET_KEY_FILE env var.

Cross-process race (api + scheduler start cold): both processes can read
the missing file, generate independent keys, and the later write would
clobber the earlier — silently destroying any rows already encrypted.
`_load_or_create_key` uses O_CREAT|O_EXCL so only one process wins; the
loser reads the winner's key.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_DEFAULT_KEY_PATH = "data/.secret_key"
_fernet: Optional[Fernet] = None


def _key_path() -> Path:
    return Path(os.getenv("TIKTOK_SECRET_KEY_FILE", _DEFAULT_KEY_PATH))


def _load_or_create_key() -> bytes:
    p = _key_path()
    if p.exists():
        return p.read_bytes()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        # O_CREAT|O_EXCL fails atomically if another process raced us to it.
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return p.read_bytes()
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except Exception:
        # Ensure a partial write does not leave an unusable key file.
        try:
            p.unlink()
        finally:
            raise
    log.info("Generated new secret key at %s", p)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def mask(plain: str) -> str:
    if not plain or len(plain) <= 8:
        return "•" * 8
    return f"{plain[:4]}{'•' * 8}{plain[-4:]}"


def _reset_for_tests() -> None:
    """Drop the cached Fernet so the next call re-reads the key file."""
    global _fernet
    _fernet = None
