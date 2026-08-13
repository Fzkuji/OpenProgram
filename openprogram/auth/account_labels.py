"""Stable account ids and human-readable account labels."""
from __future__ import annotations

import base64
import json
import unicodedata

from .types import Credential

MAX_ACCOUNT_LABEL_CHARS = 120


def normalize_account_label(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("account label must be a string")
    label = unicodedata.normalize("NFC", value).strip()
    if not label:
        if allow_empty:
            return ""
        raise ValueError("account label is required")
    if len(label) > MAX_ACCOUNT_LABEL_CHARS or any(not ch.isprintable() for ch in label):
        raise ValueError(
            f"account label must be printable and at most {MAX_ACCOUNT_LABEL_CHARS} characters"
        )
    return label


def _jwt_claims(token: object) -> dict:
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return {}


def account_identity(cred: Credential | None) -> tuple[str, str]:
    """Return ``(email, display_name)`` from non-secret OAuth identity data."""
    if cred is None:
        return "", ""
    metadata = cred.metadata if isinstance(cred.metadata, dict) else {}
    email = metadata.get("email") or metadata.get("account") or ""
    display_name = metadata.get("display_name") or metadata.get("name") or ""

    data = cred.payload.data if isinstance(cred.payload.data, dict) else {}
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    account = extra.get("account") if isinstance(extra.get("account"), dict) else {}
    email = email or account.get("email_address") or account.get("email") or ""
    display_name = (
        display_name
        or account.get("display_name")
        or account.get("name")
        or ""
    )

    claims = _jwt_claims(data.get("id_token"))
    email = email or claims.get("email") or ""
    display_name = (
        display_name
        or claims.get("name")
        or claims.get("preferred_username")
        or ""
    )
    return str(email).strip(), str(display_name).strip()


def apply_account_label(cred: Credential, requested_label: str = "") -> Credential:
    """Persist identity metadata and a label without changing ``account_id``."""
    label = normalize_account_label(requested_label, allow_empty=True)
    email, display_name = account_identity(cred)
    metadata = dict(cred.metadata or {})
    if email:
        metadata.setdefault("email", email)
    if display_name:
        metadata.setdefault("display_name", display_name)
    if label:
        metadata["label"] = label
    elif not metadata.get("label"):
        metadata["label"] = email or display_name or cred.account_id
    cred.metadata = metadata
    return cred


def effective_account_label(cred: Credential | None, account_id: str) -> str:
    metadata = (cred.metadata or {}) if cred else {}
    stored = metadata.get("label") if isinstance(metadata, dict) else ""
    try:
        stored = normalize_account_label(stored, allow_empty=True)
    except ValueError:
        stored = ""
    email, display_name = account_identity(cred)
    return stored or email or display_name or account_id


__all__ = [
    "MAX_ACCOUNT_LABEL_CHARS",
    "account_identity",
    "apply_account_label",
    "effective_account_label",
    "normalize_account_label",
]
