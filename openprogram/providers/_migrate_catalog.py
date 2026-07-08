"""One-shot migration: _catalog/<p>.json -> providers/<dir>/{provider.json,catalog.json}.

Groups rows by their own `provider` field (NOT the key prefix — see
gemini-subscription double-key). Distinct (api, base_url) pairs become named
endpoints; each model references one. Rows are kept ONE-PER-KEY (no id dedup —
gemini has same-id/different-name rows); a key whose prefix != provider gets a
`key_prefix` field so get_model's historical spelling keeps resolving.

Target directory is resolved relative to the given providers_root (hyphen->
underscore, reuse existing wire-code dir, mirrors provider_models._provider_dir's
lookup logic without importing it); the git spec file is catalog.json
(models.json is the gitignored Fetch cache — untouched).
"""
from __future__ import annotations

import json
from pathlib import Path

_SPEC_DROP = {"api", "base_url", "provider"}


def _target_dir(providers_root: Path, provider_id: str) -> Path:
    """Resolve provider id to its directory under providers_root (hyphen->underscore)."""
    for name in (provider_id, provider_id.replace("-", "_")):
        d = providers_root / name
        if d.is_dir():
            return d
    return providers_root / provider_id.replace("-", "_")


def migrate_catalog_file(catalog: dict) -> tuple[dict, list[dict]]:
    # group by row's provider field
    provider_id = None
    for row in catalog.values():
        provider_id = row.get("provider")
        break
    provider_id = provider_id or "unknown"

    # collect distinct (api, base_url), name them; most-common → "default"
    from collections import Counter
    pair_counts = Counter((r.get("api"), r.get("base_url")) for r in catalog.values())
    ordered = [p for p, _ in pair_counts.most_common()]
    ep_name: dict[tuple, str] = {}
    endpoints: dict[str, dict] = {}
    for i, (api, base) in enumerate(ordered):
        name = "default" if i == 0 else (api or f"ep{i}")
        # de-dup name collisions
        if name in endpoints and name != "default":
            name = f"{name}-{i}"
        ep_name[(api, base)] = name
        endpoints[name] = {"api": api, "base_url": base}

    # ONE row per catalog key (preserve every key + its own name/fields).
    # Deterministic order: sort by key.
    models: list[dict] = []
    for key in sorted(catalog):
        row = catalog[key]
        prefix = key.split("/", 1)[0]
        name = ep_name[(row.get("api"), row.get("base_url"))]
        spec = {k: v for k, v in row.items() if k not in _SPEC_DROP}
        if name != "default":
            spec["endpoint"] = name
        if prefix != provider_id:
            spec["key_prefix"] = prefix
        models.append(spec)

    provider_json = {"id": provider_id, "endpoints": endpoints}
    return provider_json, models


def migrate_all(catalog_dir: Path, providers_root: Path) -> list[str]:
    done = []
    for jf in sorted(catalog_dir.glob("*.json")):
        catalog = json.loads(jf.read_text(encoding="utf-8"))
        if not catalog:
            continue
        pj, models = migrate_catalog_file(catalog)
        d = _target_dir(providers_root, pj["id"])  # underscore dir, reuse existing wire-code dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "provider.json").write_text(json.dumps(pj, indent=1, ensure_ascii=False))
        (d / "catalog.json").write_text(json.dumps({"models": models}, indent=1, ensure_ascii=False))
        done.append(pj["id"])
    return done


def verify_equivalence(catalog_dir: Path, providers_root: Path) -> list[str]:
    import json as _json
    from .types import Model
    old: dict[str, Model] = {}
    for jf in sorted(catalog_dir.glob("*.json")):
        for k, row in _json.loads(jf.read_text(encoding="utf-8")).items():
            old[k] = Model.model_validate(row)
    from ._catalog_new import load_new_catalog
    new = load_new_catalog(providers_root)
    mismatched = []
    # forward: every old key reproduced byte-identically in new
    for k, m in old.items():
        n = new.get(k)
        if n is None or n.model_dump() != m.model_dump():
            mismatched.append(k)
    # reverse: new must not introduce keys absent from old (dup/spurious rows)
    for k in new:
        if k not in old:
            mismatched.append(f"EXTRA:{k}")
    return mismatched
