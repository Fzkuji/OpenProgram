"""Per-provider model store — fetch results saved as
``openprogram/providers/<provider>/models.json`` (gitignored).

Two sources, combined at read time:
  * fetch (provider official /v1/models) — authoritative "which models +
    context", saved per-provider as models.json (overwritten on each Fetch).
  * models.dev (live) — fills price / capability fields the official API
    doesn't return.

No frozen snapshot, no config custom_models. A provider with no models.json
yet falls back to models.dev's full list for that provider.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Fetched lists: one file per provider under providers/_catalog/fetched/.
# Kept in its own subdir (not _catalog/ root, which holds the static
# split data that IS in git) so fetched output is cleanly gitignored and
# never collides with static files. Provider IDs (claude-code/openai-codex)
# don't map 1:1 to code dirs, so we key by provider id here.
_CATALOG_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "providers" / "_catalog" / "fetched"
)

# claude-code / openai-codex etc. have no models.dev entry — borrow the
# standard sibling's models.dev data (same models, different wire/billing).
_SUBSCRIPTION_BORROW = {
    "claude-code": "anthropic",
    "openai-codex": "openai",
    "gemini-subscription": "google",
}


def _fallback_dir() -> Path:
    """Where to write when _catalog isn't writable (read-only install)."""
    return Path.home() / ".openprogram" / "models"


def _resolve_write_path(provider_id: str) -> Path:
    """_catalog/fetched/<provider>.json if writable, else home fallback."""
    parent = _CATALOG_DIR.parent  # providers/_catalog
    if parent.is_dir() and os.access(parent, os.W_OK):
        _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        return _CATALOG_DIR / f"{provider_id}.json"
    fb = _fallback_dir()
    fb.mkdir(parents=True, exist_ok=True)
    return fb / f"{provider_id}.json"


def _resolve_read_path(provider_id: str) -> Path | None:
    """Where this provider's fetched models live, or None if never fetched."""
    p = _CATALOG_DIR / f"{provider_id}.json"
    if p.is_file():
        return p
    fb = _fallback_dir() / f"{provider_id}.json"
    return fb if fb.is_file() else None


def save_fetched(provider_id: str, models: list[dict[str, Any]]) -> Path:
    """Overwrite this provider's models.json with the freshly-fetched list.

    Atomic write (tmp + replace). Returns the path written.
    """
    path = _resolve_write_path(provider_id)
    payload = {"provider": provider_id, "models": models}
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_fetched(provider_id: str) -> list[dict[str, Any]]:
    """The provider's saved fetched models, or [] if never fetched."""
    path = _resolve_read_path(provider_id)
    if path is None:
        return []
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f).get("models", [])
    except (OSError, json.JSONDecodeError):
        return []


def _models_dev_for(provider_id: str) -> dict[str, dict[str, Any]]:
    """models.dev models for a provider (id -> normalised row). Borrows the
    standard sibling for subscription providers. Empty on failure/offline."""
    from .sources import models_dev
    src = _SUBSCRIPTION_BORROW.get(provider_id, provider_id)
    try:
        return models_dev.list_models(src) or {}
    except Exception:
        return {}


def combined_models(provider_id: str) -> list[dict[str, Any]]:
    """Merge fetched (authoritative: which + context) with models.dev
    (price + capabilities). Falls back to models.dev's full list when the
    provider was never fetched.
    """
    md = _models_dev_for(provider_id)
    fetched = load_fetched(provider_id)

    if fetched:
        out = []
        for m in fetched:
            mid = m.get("id")
            if not mid:
                continue
            row = dict(md.get(mid, {}))  # price/caps from models.dev (if any)
            row.update({k: v for k, v in m.items() if v is not None})  # official wins
            row["id"] = mid
            out.append(row)
        return out

    # Never fetched → models.dev full list for this provider.
    return [{**row, "id": mid} for mid, row in md.items()]
