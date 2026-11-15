"""Default (implicit) failover chain — same-provider only, capped, opt-outable."""
from __future__ import annotations

import pytest

from openprogram.models.providers.utils import failover


class FakeModel:
    def __init__(self, provider: str, mid: str):
        self.provider = provider
        self.id = mid

    def __repr__(self) -> str:  # nicer assertion output
        return f"{self.provider}/{self.id}"


def _registry(*models: FakeModel) -> dict[str, FakeModel]:
    return {f"{m.provider}/{m.id}": m for m in models}


@pytest.fixture(autouse=True)
def config(monkeypatch):
    """The ``model_fallback`` block the Settings page writes (empty unless a
    test sets it), so no test reads the user's real config."""
    from openprogram.state import config as user_config

    block: dict = {}
    monkeypatch.setattr(user_config, "read_config", lambda: {"model_fallback": block} if block else {})
    return block


@pytest.fixture
def enabled(monkeypatch):
    """Install a fake ENABLED_MODELS registry (never touches user config)."""
    import openprogram.models.providers.catalog.enabled_models as em

    def _install(*models: FakeModel):
        monkeypatch.setattr(em, "ENABLED_MODELS", _registry(*models))

    return _install


def _ids(models) -> list[str]:
    return [f"{m.provider}/{m.id}" for m in models]


def test_implicit_chain_is_off_by_default(monkeypatch, enabled):
    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    primary = FakeModel("acme", "big")
    enabled(primary, FakeModel("acme", "mid"), FakeModel("other", "big"))
    assert failover.resolve_fallback_models(primary) == []


def test_same_provider_opt_in_is_same_provider_only(monkeypatch, enabled):
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "same-provider")
    primary = FakeModel("acme", "big")
    enabled(
        primary,
        FakeModel("acme", "mid"),
        FakeModel("acme", "small"),
        FakeModel("other", "big"),
    )
    assert _ids(failover.resolve_fallback_models(primary)) == ["acme/mid", "acme/small"]


def test_implicit_chain_is_capped_at_two(monkeypatch, enabled):
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "same-provider")
    primary = FakeModel("acme", "p")
    enabled(primary, *(FakeModel("acme", f"m{i}") for i in range(5)))
    assert _ids(failover.resolve_fallback_models(primary)) == ["acme/m0", "acme/m1"]


def test_provider_with_no_other_models_yields_nothing(monkeypatch, enabled):
    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    primary = FakeModel("acme", "only")
    enabled(primary, FakeModel("other", "x"))
    assert failover.resolve_fallback_models(primary) == []


@pytest.mark.parametrize("value", ["off", "none", "OFF", " None "])
def test_opt_out_disables_the_chain(monkeypatch, enabled, value):
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", value)
    primary = FakeModel("acme", "big")
    enabled(primary, FakeModel("acme", "mid"))
    assert failover.resolve_fallback_models(primary) == []


def test_explicit_list_wins_and_may_cross_providers(monkeypatch, enabled):
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "other/x, acme/mid")
    primary = FakeModel("acme", "big")
    enabled(primary, FakeModel("acme", "mid"), FakeModel("other", "x"))
    lookup = {"acme": {"mid": FakeModel("acme", "mid")}, "other": {"x": FakeModel("other", "x")}}
    monkeypatch.setattr(
        "openprogram.models.providers.get_model",
        lambda prov, mid: lookup.get(prov, {}).get(mid),
    )
    assert _ids(failover.resolve_fallback_models(primary)) == ["other/x", "acme/mid"]


def test_broken_state_never_raises(monkeypatch):
    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    import openprogram.models.providers.catalog.enabled_models as em

    class Boom(dict):
        def values(self):
            raise RuntimeError("registry exploded")

    monkeypatch.setattr(em, "ENABLED_MODELS", Boom())
    assert failover.resolve_fallback_models(FakeModel("acme", "big")) == []
    # A primary with no provider attribute at all is also survivable.
    assert failover.resolve_fallback_models(object()) == []


def test_settings_choose_same_provider(monkeypatch, enabled, config):
    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    config.update(mode="same-provider")
    primary = FakeModel("acme", "big")
    enabled(primary, FakeModel("acme", "mid"), FakeModel("other", "big"))
    assert _ids(failover.resolve_fallback_models(primary)) == ["acme/mid"]


def test_settings_list_is_tried_in_order(monkeypatch, enabled, config):
    monkeypatch.delenv("OPENPROGRAM_FALLBACK_MODELS", raising=False)
    config.update(mode="list", models=["other/x", "acme/big", "acme/mid"])
    primary = FakeModel("acme", "big")
    models = _registry(primary, FakeModel("acme", "mid"), FakeModel("other", "x"))
    import openprogram.models.providers as providers
    monkeypatch.setattr(providers, "get_model", lambda prov, mid: models.get(f"{prov}/{mid}"))
    assert _ids(failover.resolve_fallback_models(primary)) == ["other/x", "acme/mid"]


def test_environment_overrides_settings(monkeypatch, enabled, config):
    monkeypatch.setenv("OPENPROGRAM_FALLBACK_MODELS", "off")
    config.update(mode="same-provider")
    primary = FakeModel("acme", "big")
    enabled(primary, FakeModel("acme", "mid"))
    assert failover.resolve_fallback_models(primary) == []


def test_settings_reject_a_model_without_its_provider():
    from openprogram.state import settings

    assert settings.set_setting("model_fallback.models", '["gpt-5"]')["error"]
