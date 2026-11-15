"""Retired Claude subscription entry points fail without contacting Anthropic."""
import asyncio

import pytest


def test_subscription_provider_cannot_create_runtime():
    from openprogram.runtime.agent.runtime.factory import create_runtime
    with pytest.raises(ValueError, match="Anthropic requires an API key"):
        create_runtime(provider="claude-code", api_key="sk-ant-oat-test")


@pytest.mark.parametrize("provider,method", [
    ("claude-code", "pkce_oauth"), ("claude-code", "setup_token"),
    ("claude-code", "api_key"), ("anthropic", "pkce_oauth"),
    ("anthropic", "setup_token"),
])
def test_subscription_login_rejected_before_ui(provider, method):
    from openprogram.models.auth.login.login_driver import run_login
    from openprogram.models.auth.types import AuthConfigError
    with pytest.raises(AuthConfigError, match="Anthropic requires an API key"):
        asyncio.run(run_login(provider, "default", method, None, api_key="sk-ant-oat-test"))


def test_anthropic_only_offers_api_key():
    from openprogram.models.auth.login.login_method_registry import login_methods
    assert [m[0] for m in login_methods("anthropic")] == ["api_key"]
    assert login_methods("claude-code") == []


def test_subscription_token_rejected_even_when_marked_api_key():
    from openprogram.models.providers.adapters.anthropic.anthropic import _build_client
    from openprogram.models.providers.types import Model
    model = Model(id="claude-sonnet-4-6", name="Claude", api="anthropic-messages", provider="anthropic", base_url="https://api.anthropic.com")
    from openprogram.models.auth.types import AuthConfigError
    with pytest.raises(AuthConfigError, match="Anthropic requires an API key"):
        _build_client(model, "sk-ant-oat-test", is_oauth_override=False)


@pytest.mark.parametrize('source', ['options', 'model'])
def test_api_client_rejects_subscription_authorization_header(source):
    from openprogram.models.providers.adapters.anthropic.anthropic import _build_client
    from openprogram.models.providers.types import Model
    from openprogram.models.auth.types import AuthConfigError
    headers = {'Authorization': 'Bearer sk-ant-oat-SYNTHETIC'}
    model = Model(id='claude-sonnet-4-6', name='Claude', api='anthropic-messages', provider='anthropic', base_url='https://api.anthropic.com', headers=headers if source == 'model' else {})
    with pytest.raises(AuthConfigError):
        _build_client(model, 'sk-ant-api-SYNTHETIC', options_headers=headers if source == 'options' else {})


def test_store_resolver_rejects_subscription_token_in_api_key_field(monkeypatch):
    from openprogram.models.auth import resolver
    from openprogram.models.auth.types import Credential, CredentialData
    cred = Credential(provider_id='anthropic', account_id='default', kind='api_key', payload=CredentialData(kind='api_key', auth_value='sk-ant-oat-SYNTHETIC'))
    monkeypatch.setattr(resolver, 'get_credential_override', lambda _: cred)
    assert resolver.resolve_store_api_key_sync('anthropic', 'default') is None


def test_count_tokens_rejects_subscription_token_before_network(monkeypatch):
    from openprogram.models.providers.shared import anthropic_token_count as counter
    monkeypatch.setattr(counter, 'safe_client', lambda *a, **k: pytest.fail('unexpected network client'))
    assert counter.count_tokens_via_anthropic([{'role': 'user', 'content': 'test'}], 'claude-sonnet-4-6', api_key='sk-ant-oat-SYNTHETIC') is None


@pytest.mark.parametrize('module_name', ['openprogram.models.providers.streaming.stream', 'openprogram.models.providers.adapters.anthropic.anthropic', 'openprogram.models.providers.adapters.openai_completions.openai_completions'])
def test_direct_stream_rejects_unsupported_provider(module_name):
    import importlib
    from openprogram.models.auth.types import AuthConfigError
    from openprogram.models.providers.types import Model, Context, SimpleStreamOptions
    mod = importlib.import_module(module_name)
    model = Model(id='test', name='Test', provider='claude-code', api='anthropic-messages', base_url='https://example.invalid')
    async def run():
        return [event async for event in mod.stream_simple(model, Context(messages=[]), SimpleStreamOptions(api_key='sk-ant-api-SYNTHETIC'))]
    with pytest.raises(AuthConfigError):
        asyncio.run(run())
