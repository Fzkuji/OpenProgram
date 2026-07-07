"""One-shot migration: old 6-payload JSON → new CredentialData structure.

Runtime code only understands the new structure (see types._payload_from_dict).
This searches ~/.openprogram/auth/**.json, rewrites each credential's payload
in place, atomically. Idempotent: a payload already in the new shape is left
as-is. Old format is NOT supported after migration.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .types import CREDENTIAL_SCHEMA_VERSION

_TYPE_TO_KIND = {
    "ApiKeyPayload": "api_key",
    "OAuthPayload": "oauth",
    "DeviceCodePayload": "device_code",
    "CliDelegatedPayload": "cli_delegated",
    "ExternalProcessPayload": "external_process",
    "SsoPayload": "sso",
}
# Which old field became auth_value (rest go into data).
_AUTH_FIELD = {
    "ApiKeyPayload": "api_key",
    "OAuthPayload": "access_token",
    "DeviceCodePayload": "access_token",
}


def migrate_payload_dict(old: dict) -> dict:
    # Already new structure → idempotent no-op.
    if "kind" in old and "__type__" not in old:
        return old
    tname = old.get("__type__", "")
    kind = _TYPE_TO_KIND.get(tname)
    if kind is None:
        # Unknown/absent discriminator: best-effort passthrough shell.
        return {"kind": old.get("kind", ""), "auth_value": "",
                "base_url": "", "headers": {}, "data": dict(old)}
    auth_field = _AUTH_FIELD.get(tname)
    data = {k: v for k, v in old.items()
            if k not in ("__type__", auth_field)}
    return {
        "kind": kind,
        "auth_value": old.get(auth_field, "") if auth_field else "",
        "base_url": "",
        "headers": {},
        "data": data,
    }


def _migrate_file(path: Path) -> bool:
    try:
        doc = json.loads(path.read_text())
    except Exception:
        return False
    creds = doc.get("credentials")
    if not isinstance(creds, list):
        return False  # admin file (_rotation/_active/...) — no credentials
    changed = False
    for c in creds:
        p = c.get("payload")
        if isinstance(p, dict) and "__type__" in p:
            c["payload"] = migrate_payload_dict(p)
            changed = True
        # An old-format credential also carries the pre-CredentialData
        # schema version; bump it so Credential.from_dict (which requires
        # v == CREDENTIAL_SCHEMA_VERSION) accepts the rewritten dict.
        if c.get("v") != CREDENTIAL_SCHEMA_VERSION:
            c["v"] = CREDENTIAL_SCHEMA_VERSION
            changed = True
    # Some stores mirror a top-level "payload" too; migrate if present.
    top = doc.get("payload")
    if isinstance(top, dict) and "__type__" in top:
        doc["payload"] = migrate_payload_dict(top)
        changed = True
    if not changed:
        return False
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def migrate_store(root: Path | None = None) -> int:
    base = Path(root) if root else Path.home() / ".openprogram"
    auth_dir = base / "auth"
    if not auth_dir.is_dir():
        return 0
    n = 0
    for path in auth_dir.rglob("*.json"):
        if _migrate_file(path):
            n += 1
    return n
