"""Acceptance tests for the bailian → alibaba-token-plan-cn provider rename.

The provider directory moved from ``providers/bailian/`` into the existing
``providers/alibaba_token_plan_cn/`` and its ``provider.json`` id became
``alibaba-token-plan-cn`` (matching models.dev). An alias
``bailian → alibaba-token-plan-cn`` keeps old config key/enabled refs live.

Post-Task-3 the registry loads enabled models from config, so these tests
enable a couple of alibaba-token-plan-cn rows and assert the rename + alias
invariants over the rebuilt registry (the rename is about the provider id /
directory, independent of the catalogue-vs-config source).
"""
from __future__ import annotations

import pytest

from openprogram.auth.aliases import resolve
from openprogram.providers.models import get_model

from ._registry_fixture import install_registry

# Model ids known to live in this provider's models.json.
_SAMPLE_IDS = ["MiniMax-M2.5", "deepseek-v4-pro"]


@pytest.fixture(autouse=True)
def _enable_alibaba(monkeypatch):
    return install_registry(monkeypatch, {"alibaba-token-plan-cn": {"models": [
        {"id": mid, "name": mid} for mid in _SAMPLE_IDS
    ]}})


def test_registry_keyed_by_new_provider_id(_enable_alibaba):
    reg = _enable_alibaba
    assert f"alibaba-token-plan-cn/{_SAMPLE_IDS[0]}" in reg
    assert not any(k.startswith("bailian/") for k in reg)


def test_get_model_hits_new_id():
    m = get_model("alibaba-token-plan-cn", _SAMPLE_IDS[0])
    assert m is not None
    assert m.provider == "alibaba-token-plan-cn"


def test_get_model_hits_old_id_via_alias():
    assert resolve("bailian") == "alibaba-token-plan-cn"
    m = get_model("bailian", _SAMPLE_IDS[0])
    assert m is not None
    assert m.provider == "alibaba-token-plan-cn"


def test_provider_key_prefix_uses_new_id(_enable_alibaba):
    keys = [k for k in _enable_alibaba if k.startswith("alibaba-token-plan-cn/")]
    assert len(keys) == len(_SAMPLE_IDS)


def test_provider_json_uses_cn_beijing_base_url():
    """The CN token plan must point at cn-beijing, never the international
    ap-southeast (alibaba-token-plan) or the dashscope coding-plan endpoint —
    don't conflate the three Alibaba billing variants."""
    import json
    from pathlib import Path

    import openprogram.providers as _p

    pj = Path(_p.__file__).parent / "alibaba_token_plan_cn" / "provider.json"
    ep = json.loads(pj.read_text())["endpoints"]["default"]
    assert ep["base_url"] == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert "ap-southeast" not in ep["base_url"]  # not the international token plan
    assert "coding" not in ep["base_url"]  # not the coding plan


def test_lobe_icon_map_carries_token_plan_logo():
    """The settings sidebar resolves logos by provider id via web/.../lobe-icons.ts.
    Both Alibaba token-plan ids must map to the alibaba slug, else the row
    renders logo-less (the 'china' entry the user reported)."""
    from pathlib import Path

    import openprogram

    root = Path(openprogram.__file__).parent.parent
    icons = (root / "web/components/settings/lobe-icons.ts").read_text()
    assert '"alibaba-token-plan-cn": { slug: "alibaba"' in icons
    assert '"alibaba-token-plan": { slug: "alibaba"' in icons
