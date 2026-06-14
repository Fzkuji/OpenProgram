"""
Auto-generated model catalog — mirrors TypeScript models.generated.ts.

DO NOT EDIT the data by hand. The 741 model definitions live in the
sibling ``models_generated.json`` (pure data); this module just loads
that file and rebuilds the ``MODELS`` dict so the public interface
(``from openprogram.providers.models_generated import MODELS``) is
unchanged for the ~9 call sites that use it.

Why JSON, not 8900 lines of ``Model(...)`` calls: it's catalog data,
not code. Keeping it as data makes regeneration a file swap and stops
the repo's longest "source" file from being a wall of constructors.
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Model

_DATA_PATH = Path(__file__).with_suffix(".json")


def _load() -> dict[str, Model]:
    with _DATA_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {key: Model.model_validate(row) for key, row in raw.items()}


MODELS: dict[str, Model] = _load()
