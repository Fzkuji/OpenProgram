"""Shared helper: rebuild MODEL_REGISTRY from an injected config.

Post-Task-3 the runtime registry loads only the user's enabled models
(config ``providers.<p>.models`` spec rows). Tests that used to lean on the
full static catalogue inject a minimal config here and rebuild the registry
from it, patching every binding so ``get_model`` / ``get_providers`` /
runtime construction all see the same dict.
"""
from __future__ import annotations

import openprogram.providers._config_read as cr
import openprogram.providers.models as pm
import openprogram.providers.models_generated as mg


def install_registry(monkeypatch, providers_cfg: dict) -> dict:
    monkeypatch.setattr(cr, "read_providers_config", lambda: providers_cfg)
    reg = mg._load()
    monkeypatch.setattr(mg, "MODEL_REGISTRY", reg)
    monkeypatch.setattr(pm, "MODEL_REGISTRY", reg)
    return reg
