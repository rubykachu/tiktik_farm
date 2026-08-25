"""Account fields that may be included in a pipeline's Telegram notification.

Single source of truth: the API validates `telegram_attach_fields` against
ATTACH_FIELD_KEYS, the scheduler renders values through `resolve()`, and the
webapp fetches the labelled list from GET /api/telegram/attach-fields so a new
field only has to be added here.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from api.models import Account
from api.services import secrets

log = logging.getLogger(__name__)


def _plain_login(account: Account) -> Optional[str]:
    return account.login


def _decrypted_password(account: Account) -> Optional[str]:
    if not account.login_password_encrypted:
        return None
    try:
        return secrets.decrypt(account.login_password_encrypted)
    except Exception:
        log.warning("Failed to decrypt login_password for account %s", account.id)
        return None


# key -> (human label shown in the webapp, value getter)
ATTACH_FIELDS: dict[str, tuple[str, Callable[[Account], Optional[str]]]] = {
    "login": ("Tên đăng nhập", _plain_login),
    "login_password": ("Mật khẩu", _decrypted_password),
}

ATTACH_FIELD_KEYS = frozenset(ATTACH_FIELDS)


def resolve(account: Account, field: str) -> Optional[str]:
    """Return the account's value for `field`, or None if unknown/unset."""
    entry = ATTACH_FIELDS.get(field)
    if entry is None:
        return None
    return entry[1](account)


def label(field: str) -> str:
    entry = ATTACH_FIELDS.get(field)
    return entry[0] if entry else field
