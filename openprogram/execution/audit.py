"""Redaction rules for append-only execution audit records."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


_SENSITIVE_PARTS = frozenset({
    "answer", "authorization", "credential", "environment", "env", "output",
    "password", "prompt", "secret", "token", "checkpoint",
})


def redact_audit_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a stable, non-reversible summary suitable for audit storage."""
    return _redact(value or {})


def _redact(value: Any, key: str = "") -> Any:
    key_parts = {part for part in key.lower().replace("-", "_").split("_") if part}
    if key_parts & _SENSITIVE_PARTS:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return {
            "redacted": True,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
        }
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
