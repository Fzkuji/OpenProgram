"""
Model catalog — dual-source: legacy ``_catalog/`` plus self-contained
``providers/<p>/`` dirs.

The model definitions live as data (not 9000 lines of ``Model(...)``
calls). Historically they were split into ``_catalog/<provider>.json``
— one small file per provider. Providers are being migrated to
self-contained ``providers/<p>/provider.json`` + ``providers/<p>/catalog.json``
dirs (see ``_catalog_new.load_new_catalog``); during the migration both
sources are merged, with the new self-contained dir winning on key
collision so a migrated provider's data takes over from its legacy
file without needing the legacy file deleted yet. ``_load()`` merges
both into the ``MODELS`` dict, keyed ``"<provider>/<id>"`` exactly as
before, so the public interface (``from
openprogram.providers.models_generated import MODELS``) is unchanged
for every call site.

A Fetch (webui model catalog) rewrites the relevant per-provider
catalog file in place — refresh = a file swap, no hand maintenance,
and no second copy in config.json to drift against.
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Model

_CATALOG_DIR = Path(__file__).parent / "_catalog"
_PROVIDERS_DIR = Path(__file__).parent


def _load() -> dict[str, Model]:
    merged: dict[str, Model] = {}
    # legacy _catalog (fallback, removed in a later task)
    if _CATALOG_DIR.is_dir():
        # Sorted for deterministic load order; later files can't collide
        # because keys are "<provider>/<id>" and each file holds one provider.
        for jf in sorted(_CATALOG_DIR.glob("*.json")):
            try:
                with jf.open(encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue  # a broken per-provider file shouldn't kill the catalog
            for key, row in raw.items():
                merged[key] = Model.model_validate(row)
    # new self-contained providers/<p>/ — wins on key collision
    try:
        from ._catalog_new import load_new_catalog
        merged.update(load_new_catalog(_PROVIDERS_DIR))
    except Exception:
        pass
    return merged


MODELS: dict[str, Model] = _load()
