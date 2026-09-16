from types import SimpleNamespace

from openprogram.providers import models
from openprogram.providers import _config_read
from openprogram.providers.sources import models_dev


def test_official_grok_priority_is_route_scoped(monkeypatch):
    from openprogram.webui._model_listing.listing import supports_fast
    monkeypatch.setattr(models_dev, 'lookup', lambda *a: {})
    monkeypatch.setattr(_config_read, 'read_providers_config', lambda: {})
    monkeypatch.setattr(models, 'get_model', lambda p, m: SimpleNamespace(
        id=m, provider=p, api='openai-completions', base_url=(
            'https://api.x.ai/v1' if p == 'xai' else 'https://cli-chat-proxy.grok.com/v1')))
    assert supports_fast('xai', 'grok-4.6')
    assert supports_fast('xai-subscription', 'grok-4.6')
    assert not supports_fast('xai-subscription', 'grok-4.5')
    from openprogram.providers.fast import fast_capability
    sub = SimpleNamespace(
        id='grok-4.6', provider='xai-subscription',
        api='openai-completions', base_url='https://api.x.ai/v1')
    assert fast_capability('xai-subscription', sub.id, model=sub) == {
        'status': 'supported', 'source': 'xai-subscription'}
    older = SimpleNamespace(
        id='grok-4.5', provider='xai-subscription',
        api='openai-completions', base_url='https://cli-chat-proxy.grok.com/v1')
    assert fast_capability('xai-subscription', older.id, model=older) == {
        'status': 'unknown', 'source': 'subscription-unverified'}
    assert not supports_fast('xai', 'grok-4.5')
    custom = SimpleNamespace(
        id='grok-4.6', provider='custom',
        api='openai-completions', base_url='https://gateway.example/v1')
    assert not supports_fast('custom', 'grok-4.6')
    assert fast_capability('custom', custom.id, model=custom)['status'] == 'unknown'


def test_fast_off_overrides_priority_and_explicit_false(monkeypatch):
    from openprogram.providers.fast import fast_capability, resolve_service_tier
    monkeypatch.setattr(_config_read, 'read_providers_config', lambda: {
        'xai': {'models': [{'id': 'grok-4.6', 'fast': False}]},
        'xai-subscription': {'models': [{'id': 'grok-4.6', 'fast': False}]}})
    m = SimpleNamespace(id='grok-4.6', provider='xai', api='openai-completions', base_url='https://api.x.ai/v1')
    assert fast_capability('xai', m.id, model=m)['status'] == 'unsupported'
    assert resolve_service_tier(m, 'default') == 'default'
    assert resolve_service_tier(m, 'priority') is None
    sub = SimpleNamespace(
        id='grok-4.6', provider='xai-subscription',
        api='openai-completions', base_url='https://cli-chat-proxy.grok.com/v1')
    assert fast_capability('xai-subscription', sub.id, model=sub)['status'] == 'unsupported'
    assert resolve_service_tier(sub, 'priority') is None


def test_response_tier_evidence_survives_usage_projection():
    from openprogram.agent.internals._event_parsing import extract_usage
    from openprogram.providers.types import Usage
    for actual, expected in [('priority', 'priority'), ('default', 'default'), (None, 'unreported')]:
        usage = Usage(input=1, requested_service_tier='priority', service_tier=actual)
        assert extract_usage(SimpleNamespace(usage=usage))['service_tiers'] == [expected]


def test_xai_reported_cost_is_not_replaced_by_standard_catalogue():
    from openprogram.providers.openai_completions.openai_completions import _usage_from_chunk
    from openprogram.usage.recorder import _cost_from_model
    usage = _usage_from_chunk(SimpleNamespace(prompt_tokens=10, completion_tokens=5, cost_in_usd_ticks=37756000))
    cost, source = _cost_from_model(SimpleNamespace(cost=None), usage)
    assert source == 'provider_reported'
    assert cost['cost_total'] == 0.0037756
