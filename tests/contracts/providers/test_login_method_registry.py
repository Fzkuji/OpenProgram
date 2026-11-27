"""The login-method registry is the one description of how each provider is
connected; every way it offers can run, and the surfaces read only it
(openprogram/models/auth/login/login_method_registry.py)."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.auth import provider_policy
from openprogram.models.auth.login import login_driver
from openprogram.models.auth.login import login_method_registry as reg
from openprogram.models.auth.types import AuthConfigError

METHODS = {"api_key", "import_from_cli"}
CHECKS = {"openai_bearer", "openrouter_key", "anthropic_native", "anthropic_compat",
          "google_query", "login", "cloud"}


@pytest.mark.parametrize("provider", reg.listed_providers())
def test_each_entry_names_known_methods_and_checks(provider):
    entry = reg.provider_auth(provider)
    ids = [m.id for m in entry.methods]
    assert set(ids) <= METHODS and len(ids) == len(set(ids))
    assert reg.check_style(provider) in CHECKS
    for method in entry.methods:
        row = method.payload()
        assert row["label"] and row["label_zh"] and row["description"] and row["description_zh"]
    if entry.check == "login":
        assert "api_key" not in ids, "a signed-in provider is checked through its sign-in"


@pytest.mark.parametrize("provider", reg.listed_providers())
def test_every_offered_sign_in_has_a_handler(provider):
    for method in reg.login_method_list(provider):
        if method.id == "import_from_cli":
            from openprogram.models.auth.login import credential_login
            try:
                credential_login.import_from_cli(provider)
            except AuthConfigError as exc:
                assert "no import-from-CLI adapter" not in str(exc)
        else:
            assert method.id == "api_key"


def test_a_method_the_registry_does_not_offer_is_refused():
    with pytest.raises(AuthConfigError):
        asyncio.run(login_driver.run_login("openai", "telepathy", _Ui()))


def test_anthropic_is_reached_with_an_api_key_only():
    assert [m.id for m in reg.login_method_list("anthropic")] == ["api_key"]
    with pytest.raises(AuthConfigError):
        asyncio.run(login_driver.run_login("anthropic", "pkce_oauth", _Ui()))


def test_each_key_variable_belongs_to_the_provider_that_reads_it_first():
    for provider in reg.listed_providers():
        for name in reg.provider_auth(provider).env_vars or ():
            owner = reg.provider_for_env_var(name)
            assert owner is not None
            assert name in reg.env_vars(owner)


def test_the_credentials_api_describes_a_provider_from_the_registry():
    described = reg.describe("openai")
    assert [m["id"] for m in described["methods"]] == ["api_key"]
    assert described["env_vars"] == ["OPENAI_API_KEY"]
    assert described["check"] == "openai_bearer" and described["keys_url"]
    unlisted = reg.describe("some-community-provider")
    assert [m["id"] for m in unlisted["methods"]] == ["api_key"]


def test_the_cli_offers_the_registry_methods_in_order():
    from openprogram.models.auth.login.login_method_registry import login_methods
    assert [m for m, _ in login_methods("openai")] == ["api_key"]
    assert reg.default_method("openai") == "api_key"


@pytest.mark.parametrize("provider", sorted(provider_policy.WITHDRAWN_PROVIDERS))
def test_a_withdrawn_provider_offers_no_way_in(provider):
    assert provider not in reg.listed_providers()
    assert reg.login_method_list(provider) == []
    assert reg.login_methods(provider) == []
    assert reg.company_of(provider) is None
    with pytest.raises(AuthConfigError):
        reg.default_method(provider)


def test_each_provider_is_one_way_of_at_most_one_company():
    ways = [w.provider for company in reg.COMPANIES for w in company.ways]
    assert len(ways) == len(set(ways))
    assert len({c.id for c in reg.COMPANIES}) == len(reg.COMPANIES)
    # A company of one way is no company: its provider keeps its own card.
    assert all(len(company.ways) > 1 for company in reg.COMPANIES)
    assert all(w.title and w.title_zh and w.short and w.short_zh for c in reg.COMPANIES for w in c.ways)


def test_a_company_names_its_ways_and_offers_some_as_tabs():
    company, way = reg.company_of("kimi-coding")
    assert (company.id, company.label, way.title, way.short) == ("moonshotai", "Moonshot AI", "Kimi Coding plan", "Kimi Coding")
    assert reg.company_of("opencode-go")[1].title == "Go · monthly subscription"
    assert reg.company_of("deepseek") is None
    assert reg.offered_ways("kimi-coding") == ["moonshotai", "moonshotai-cn"]
    assert reg.offered_ways("opencode") == []


class _Ui:
    async def open_url(self, url):  # pragma: no cover - never reached
        pass

    async def prompt(self, message, *, secret=False):
        return ""

    async def show_progress(self, message):
        pass

    async def show_code(self, user_code, verification_uri):  # pragma: no cover
        pass
