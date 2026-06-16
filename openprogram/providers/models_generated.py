"""
Model catalog — one JSON file per provider under ``_catalog/``.

The model definitions live as data (not 9000 lines of ``Model(...)``
calls), split into ``_catalog/<provider>.json`` — one small file per
provider, so a provider's models sit in their own file instead of one
giant shared blob. ``_load()`` scans the directory and merges them into
the ``MODELS`` dict, keyed ``"<provider>/<id>"`` exactly as before, so
the public interface (``from openprogram.providers.models_generated
import MODELS``) is unchanged for every call site.

A Fetch (webui model catalog) rewrites the relevant
``_catalog/<provider>.json`` in place — refresh = a file swap, no hand
maintenance, and no second copy in config.json to drift against.
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Model

_CATALOG_DIR = Path(__file__).parent / "_catalog"


def _load() -> dict[str, Model]:
    merged: dict[str, Model] = {}
    if not _CATALOG_DIR.is_dir():
        return merged
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
    return merged


MODELS: dict[str, Model] = _load()
