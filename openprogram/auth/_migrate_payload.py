"""On-load migration of stored credential JSON to the current schema.

Runtime code only understands the current shape; everything older is
rewritten here, in place and atomically, before it reaches
``Credential.from_dict``. Two steps, applied in order:

  * **v1 → v2** — old 6-payload JSON (carrying a ``__type__``
    discriminator) becomes the flat :class:`CredentialData` structure
    (see ``types._payload_from_dict``).
  * **v2 → v3** — the two auth renames land on disk: the credential
    ``kind`` value ``"external_process"`` becomes ``"credential_process"``,
    and the ``profile_id`` field (on pools and credentials alike) becomes
    ``account_id``.

Every step is idempotent: data already in the current shape is left
untouched, so a repeated load is a cheap no-op. Old formats are not
supported after migration — reads go through the migrated shape only.
"""

from __future__ import annotations

import json
from pathlib import Path

from .types import CREDENTIAL_SCHEMA_VERSION

_TYPE_TO_KIND = {
    "ApiKeyPayload": "api_key",
    "OAuthPayload": "oauth",
    "DeviceCodePayload": "device_code",
    "CliDelegatedPayload": "cli_delegated",
    "ExternalProcessPayload": "credential_process",
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
        return {
            "kind": old.get("kind", ""),
            "auth_value": "",
            "base_url": "",
            "headers": {},
            "data": dict(old),
        }
    auth_field = _AUTH_FIELD.get(tname)
    data = {k: v for k, v in old.items() if k not in ("__type__", auth_field)}
    return {
        "kind": kind,
        "auth_value": old.get(auth_field, "") if auth_field else "",
        "base_url": "",
        "headers": {},
        "data": data,
    }


# v2 → v3: the credential kind that shells out to a helper took AWS's
# name for the same concept. Old files still say "external_process".
_KIND_V2_TO_V3 = {"external_process": "credential_process"}


def _migrate_kind_and_account(doc: dict) -> bool:
    """Apply the v2 → v3 renames to one loaded pool document, in place.

    Two independent renames, both idempotent:

      * ``kind: "external_process"`` → ``"credential_process"``, on the
        credential and on its nested payload (they carry the kind twice).
      * ``profile_id`` → ``account_id``, on the pool and on every
        credential. ``fallback_chain`` entries are positional pairs, so
        they need no rewriting.

    Returns True iff anything changed.
    """
    changed = False

    def _rename_account(node: dict) -> None:
        nonlocal changed
        if "profile_id" in node:
            # setdefault, not overwrite: if a half-migrated file somehow
            # carries both, the new field is the authority.
            node.setdefault("account_id", node["profile_id"])
            node.pop("profile_id")
            changed = True

    def _rename_kind(node: dict) -> None:
        nonlocal changed
        new = _KIND_V2_TO_V3.get(node.get("kind"))
        if new is not None:
            node["kind"] = new
            changed = True

    _rename_account(doc)
    for c in doc.get("credentials") or []:
        if not isinstance(c, dict):
            continue
        _rename_account(c)
        _rename_kind(c)
        payload = c.get("payload")
        if isinstance(payload, dict):
            _rename_kind(payload)
    return changed


def _migrate_file(path: Path, *, root: Path | None = None) -> bool:
    storage_root = root or path.parents[2]
    from openprogram.credential_files import (
        _private_atomic_write,
        _private_file_lock,
        _read_private_bytes,
    )

    with _private_file_lock(path, root=storage_root, timeout=15):
        try:
            raw = _read_private_bytes(path, root=storage_root)
            if raw is None:
                return False
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not _migrate_document(doc):
            return False
        payload = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
        _private_atomic_write(
            path,
            lambda handle: handle.write(payload),
            root=storage_root,
            lock_timeout=15,
        )
        return True


def _migrate_document(doc: dict) -> bool:
    creds = doc.get("credentials")
    if not isinstance(creds, list):
        return False  # admin file (_rotation/_active/...) — no credentials
    changed = False
    for c in creds:
        p = c.get("payload")
        # Only an old-format payload (the ``__type__`` discriminator) is ours
        # to migrate. Rewrite it AND bump its schema version together, so
        # Credential.from_dict (which requires v == CREDENTIAL_SCHEMA_VERSION)
        # accepts the rewritten dict. A credential WITHOUT ``__type__`` is left
        # entirely alone — including its ``v`` — so a genuinely corrupt or
        # future-versioned file still fails loudly in from_dict instead of
        # being silently "upgraded" here.
        if isinstance(p, dict) and "__type__" in p:
            c["payload"] = migrate_payload_dict(p)
            c["v"] = CREDENTIAL_SCHEMA_VERSION
            changed = True
    # Some stores mirror a top-level "payload" too; migrate if present.
    top = doc.get("payload")
    if isinstance(top, dict) and "__type__" in top:
        doc["payload"] = migrate_payload_dict(top)
        changed = True
    # v2 → v3 renames. Runs for every file (not just ones that needed the
    # payload rewrite above), since a store written at v2 has the new
    # payload shape but the old kind value and field name.
    if _migrate_kind_and_account(doc):
        for c in creds:
            if isinstance(c, dict):
                c["v"] = CREDENTIAL_SCHEMA_VERSION
        changed = True
    return changed


def migrate_store(root: Path | None = None) -> int:
    base = Path(root) if root else Path.home() / ".openprogram"
    auth_dir = base / "auth"
    if not auth_dir.is_dir():
        return 0
    n = 0
    for path in auth_dir.rglob("*.json"):
        if _migrate_file(path, root=base):
            n += 1
    return n
