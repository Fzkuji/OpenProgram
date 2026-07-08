"""Shared helper: rebuild ENABLED_MODELS from an injected config.

Post-Task-3 the runtime registry loads only the user's enabled models
(config ``providers.<p>.models`` spec rows). Tests that used to lean on the
full static catalogue inject a minimal config here and rebuild the registry
from it, patching every binding so ``get_model`` / ``get_providers`` /
runtime construction all see the same dict.
"""
from __future__ import annotations

import openprogram.providers._config_read as cr
import openprogram.providers.models as pm
import openprogram.providers.enabled_models as mg


def install_registry(monkeypatch, providers_cfg: dict) -> dict:
    monkeypatch.setattr(cr, "read_providers_config", lambda: providers_cfg)
    reg = mg._load()
    monkeypatch.setattr(mg, "ENABLED_MODELS", reg)
    monkeypatch.setattr(pm, "ENABLED_MODELS", reg)
    return reg
