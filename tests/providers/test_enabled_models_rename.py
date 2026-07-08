"""Task 7 smoke test: the enabled-models rename landed and the old names
are gone. Pure import-surface check — no config, no network."""
import importlib

import pytest


def test_enabled_models_public_surface():
    mod = importlib.import_module("openprogram.providers.enabled_models")
    assert isinstance(mod.ENABLED_MODELS, dict)
    assert callable(mod.reload)
    # reload returns the same mutable dict object (in-place semantics)
    assert mod.reload() is mod.ENABLED_MODELS


@pytest.mark.parametrize("name", [
    "openprogram.providers.models_generated",
    "openprogram.providers.thinking_catalog",
])
def test_retired_modules_are_gone(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(name)


def test_thinking_fields_folded_into_thinking_spec():
    spec = importlib.import_module("openprogram.providers.thinking_spec")
    assert callable(spec.derive_thinking_fields)
    assert callable(spec.apply_thinking_fields)
