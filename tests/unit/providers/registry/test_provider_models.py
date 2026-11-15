"""models.dev enrichment lookup for the live browse path."""
from __future__ import annotations

from openprogram.models.listing import provider_models as pm


def test_unsupported_provider_has_no_models(monkeypatch):
    """Subscription providers borrow the standard sibling's models.dev data."""
    seen = {}

    def _fake_list(src):
        seen["src"] = src
        return {"claude-opus-4-8": {"input_cost": 5.0}}

    from openprogram.models.providers.catalog.sources import models_dev
    monkeypatch.setattr(models_dev, "list_models", _fake_list)
    out = pm._models_dev_for("claude-code")
    assert seen == {}
    assert out == {}
