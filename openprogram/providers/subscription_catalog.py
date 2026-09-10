"""Persistent last-known-good catalogues for subscription providers."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

SUBSCRIPTION_PROVIDERS = frozenset({
    "openai-codex",
    "xai-subscription",
    "claude-code",
})
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_SAFE_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _cache_path(provider_id: str) -> Path:
    if not _SAFE_PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("invalid provider id")
    from openprogram.paths import get_state_dir

    return get_state_dir() / "cache" / "subscription-models" / f"{provider_id}.json"


def load_catalog(provider_id: str) -> tuple[list[dict[str, Any]], float | None]:
    """Return a validated cached catalogue and its fetch timestamp."""
    if provider_id not in SUBSCRIPTION_PROVIDERS:
        return [], None
    path = _cache_path(provider_id)
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES or path.is_symlink():
            return [], None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return [], None
    if not isinstance(payload, dict) or payload.get("provider") != provider_id:
        return [], None
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return [], None
    models = [dict(row) for row in raw_models if isinstance(row, dict) and row.get("id")]
    fetched_at = payload.get("fetched_at")
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        fetched_at = None
    return models, float(fetched_at) if fetched_at is not None else None


def save_catalog(provider_id: str, models: list[dict[str, Any]]) -> None:
    """Atomically persist a successful, non-empty normalized catalogue."""
    if provider_id not in SUBSCRIPTION_PROVIDERS or not models:
        return
    clean = [dict(row) for row in models if isinstance(row, dict) and row.get("id")]
    if not clean:
        return
    payload = {
        "schema_version": 1,
        "provider": provider_id,
        "fetched_at": time.time(),
        "models": clean,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    if len(encoded) > _MAX_CACHE_BYTES:
        return
    path = _cache_path(provider_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def catalog_is_stale(provider_id: str, max_age_s: float) -> bool:
    models, fetched_at = load_catalog(provider_id)
    return not models or fetched_at is None or time.time() - fetched_at >= max_age_s


__all__ = [
    "SUBSCRIPTION_PROVIDERS",
    "catalog_is_stale",
    "load_catalog",
    "save_catalog",
]
