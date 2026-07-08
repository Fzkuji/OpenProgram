"""New self-contained catalog loader: providers/<p>/provider.json + models.json.

provider.json declares endpoint groups {name: {api, base_url}}; each model in
models.json references an endpoint (default "default") from which its api +
base_url are filled. Runs alongside the legacy _catalog/ loader during
migration (see models_generated._load).

models.json is the git-tracked run spec (thinking_levels/cost/compat, etc.).
It is DISTINCT from providers/<p>/models.fetched.json, which is the gitignored
Fetch cache (models.dev-shaped) and is untouched here.
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Model


def _build_model(row: dict, provider_id: str, endpoints: dict) -> Model:
    ep_name = row.get("endpoint", "default")
    ep = endpoints.get(ep_name) or endpoints.get("default") or {}
    data = dict(row)
    data.pop("endpoint", None)
    data.pop("key_prefix", None)
    data["provider"] = provider_id
    data["api"] = ep.get("api", "openai-completions")
    data["base_url"] = ep.get("base_url", "")
    return Model.model_validate(data)


def load_provider_dir(provider_dir: Path) -> dict[str, Model]:
    pj = provider_dir / "provider.json"
    cj = provider_dir / "models.json"
    if not pj.is_file():
        return {}
    try:
        pcfg = json.loads(pj.read_text(encoding="utf-8"))
        models = json.loads(cj.read_text(encoding="utf-8")).get("models", []) if cj.is_file() else []
    except (OSError, json.JSONDecodeError):
        return {}
    provider_id = pcfg.get("id") or provider_dir.name
    endpoints = pcfg.get("endpoints") or {}
    out: dict[str, Model] = {}
    for row in models:
        try:
            m = _build_model(row, provider_id, endpoints)
        except Exception:
            continue
        prefix = row.get("key_prefix") or provider_id
        out[f"{prefix}/{m.id}"] = m
    return out


def load_new_catalog(providers_root: Path) -> dict[str, Model]:
    merged: dict[str, Model] = {}
    if not providers_root.is_dir():
        return merged
    for d in sorted(p for p in providers_root.iterdir() if p.is_dir()):
        merged.update(load_provider_dir(d))
    return merged
