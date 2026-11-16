"""What a sign-in says about its account, and the label a credential is shown with."""
from __future__ import annotations

import base64
import json
import unicodedata

from ..types import Credential

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


_OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"


def token_identity(id_token: object) -> dict:
    """``{"email", "plan"}`` an OpenID ``id_token`` names (empty values left
    out). The plan is ChatGPT's ``chatgpt_plan_type`` claim, when there is one."""
    claims = _jwt_claims(id_token)
    out = {}
    if claims.get("email"):
        out["email"] = str(claims["email"]).strip()
    openai = claims.get(_OPENAI_AUTH_CLAIM) if isinstance(claims.get(_OPENAI_AUTH_CLAIM), dict) else {}
    plan = openai.get("chatgpt_plan_type") or claims.get(f"{_OPENAI_AUTH_CLAIM}.chatgpt_plan_type")
    if plan:
        out["plan"] = str(plan).strip()
    return out


def file_identity(path: object) -> dict:
    """What another tool's sign-in file says about its account: the email
    and plan in its ``id_token`` (``tokens.id_token`` for the Codex CLI,
    ``id_token`` for the Gemini CLI). Empty when the file cannot be read."""
    if not path:
        return {}
    from pathlib import Path
    try:
        data = json.loads(Path(str(path)).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    return token_identity(tokens.get("id_token") or data.get("id_token"))


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


def identity_of(cred: Credential) -> dict:
    """``{"email", "name", ...}`` a sign-in carries, merged over what the
    credential already records; empty values are left out."""
    email, display_name = account_identity(cred)
    identity = dict(cred.identity or {})
    if email:
        identity.setdefault("email", email)
    if display_name:
        identity.setdefault("name", display_name)
    return {k: v for k, v in identity.items() if v}


def apply_account_label(cred: Credential, requested_label: str = "") -> Credential:
    """Record the sign-in's identity on ``cred`` and set its label when one
    was asked for."""
    label = normalize_account_label(requested_label, allow_empty=True)
    cred.identity = identity_of(cred)
    if label:
        cred.label = label
    return cred


__all__ = [
    "MAX_ACCOUNT_LABEL_CHARS",
    "account_identity",
    "apply_account_label",
    "file_identity",
    "identity_of",
    "normalize_account_label",
    "token_identity",
]
