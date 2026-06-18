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

_PROVIDERS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "providers"
)

# claude-code / openai-codex etc. have no models.dev entry — borrow the
# standard sibling's models.dev data (same models, different wire/billing).
_SUBSCRIPTION_BORROW = {
    "claude-code": "anthropic",
    "openai-codex": "openai",
    "gemini-subscription": "google",
}


def _provider_dir(provider_id: str) -> Path:
    """Resolve provider id to its directory under providers/."""
    for name in (provider_id, provider_id.replace("-", "_")):
        d = _PROVIDERS_DIR / name
        if d.is_dir():
            return d
    # Directory doesn't exist yet — create it (new community provider)
    d = _PROVIDERS_DIR / provider_id.replace("-", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_write_path(provider_id: str) -> Path:
    return _provider_dir(provider_id) / "models.json"


def _resolve_read_path(provider_id: str) -> Path | None:
    p = _provider_dir(provider_id) / "models.json"
    return p if p.is_file() else None


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
