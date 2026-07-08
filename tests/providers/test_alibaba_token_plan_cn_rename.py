"""Acceptance tests for the bailian → alibaba-token-plan-cn provider rename.

The provider directory moved from ``providers/bailian/`` into the existing
``providers/alibaba_token_plan_cn/`` and its ``provider.json`` id became
``alibaba-token-plan-cn`` (matching models.dev). An alias
``bailian → alibaba-token-plan-cn`` keeps old config key/enabled refs live.
"""
from __future__ import annotations

from openprogram.auth.aliases import resolve
from openprogram.providers.models import get_model
from openprogram.providers.models_generated import MODEL_REGISTRY

# A model id known to live in this provider's models.json.
_SAMPLE_ID = "qwen3.7-max"


def test_registry_keyed_by_new_provider_id():
    assert f"alibaba-token-plan-cn/{_SAMPLE_ID}" in MODEL_REGISTRY
    assert not any(k.startswith("bailian/") for k in MODEL_REGISTRY)


def test_get_model_hits_new_id():
    m = get_model("alibaba-token-plan-cn", _SAMPLE_ID)
    assert m is not None
    assert m.provider == "alibaba-token-plan-cn"


def test_get_model_hits_old_id_via_alias():
    assert resolve("bailian") == "alibaba-token-plan-cn"
    m = get_model("bailian", _SAMPLE_ID)
    assert m is not None
    assert m.provider == "alibaba-token-plan-cn"


def test_provider_model_count_preserved():
    keys = [k for k in MODEL_REGISTRY if k.startswith("alibaba-token-plan-cn/")]
    assert len(keys) == 14
