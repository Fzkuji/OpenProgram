"""A provider's hand-written default model gives way to one the catalogue lists."""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.models.providers.catalog import models as catalog_models
from openprogram.runtime.agent.runtime import factory

ENTRY = {"model_namespace": "example", "default_model": "old-1"}


def _known(monkeypatch, ids):
    monkeypatch.setattr(catalog_models, "get_models",
                        lambda provider=None: [SimpleNamespace(id=model_id) for model_id in ids])


def test_a_default_the_catalogue_lists_is_kept(monkeypatch):
    _known(monkeypatch, ["new-2", "old-1"])
    assert factory._table_default(ENTRY) == "old-1"


def test_a_default_that_left_the_catalogue_gives_way_to_a_listed_model(monkeypatch):
    _known(monkeypatch, ["new-2", "new-3"])
    assert factory._table_default(ENTRY) == "new-2"


def test_an_empty_or_unreadable_catalogue_keeps_the_default(monkeypatch):
    _known(monkeypatch, [])
    assert factory._table_default(ENTRY) == "old-1"

    def broken(provider=None):
        raise OSError("catalogue unreadable")

    monkeypatch.setattr(catalog_models, "get_models", broken)
    assert factory._table_default(ENTRY) == "old-1"
