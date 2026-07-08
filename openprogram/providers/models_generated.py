"""
Model catalog — self-contained ``providers/<p>/`` dirs.

The model definitions live as data (not 9000 lines of ``Model(...)``
calls). Each provider is a self-contained ``providers/<p>/provider.json``
+ ``providers/<p>/catalog.json`` pair (see ``_catalog_new.load_new_catalog``).
``_load()`` builds the ``MODELS`` dict, keyed ``"<provider>/<id>"`` exactly
as before, so the public interface (``from
openprogram.providers.models_generated import MODELS``) is unchanged
for every call site.

A Fetch (webui model catalog) rewrites the relevant per-provider
catalog file in place — refresh = a file swap, no hand maintenance,
and no second copy in config.json to drift against.
"""
from __future__ import annotations

from pathlib import Path

from .types import Model

_PROVIDERS_DIR = Path(__file__).parent


def _load() -> dict[str, Model]:
    merged: dict[str, Model] = {}
    try:
        from ._catalog_new import load_new_catalog
        merged.update(load_new_catalog(_PROVIDERS_DIR))
    except Exception:
        pass
    return merged


MODELS: dict[str, Model] = _load()
