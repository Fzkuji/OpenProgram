import asyncio
from types import SimpleNamespace as NS

import pytest

from openprogram.agent.internals._event_parsing import extract_usage
from openprogram.providers.anthropic import anthropic
from openprogram.providers.types import Model, Context, SimpleStreamOptions


@pytest.mark.parametrize('tier', ['priority', 'default'])
def test_fast_request_without_reported_tier_is_unconfirmed(monkeypatch, tier):
    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def get_final_message(self):
            return NS(usage=NS(input_tokens=10, output_tokens=5), stop_reason='end_turn')

    params = {}

    def stream(**kwargs):
        params.update(kwargs)
        return Stream()

    monkeypatch.setattr('openprogram.auth.usage.acquire_pooled', lambda *args: None)
    monkeypatch.setattr(anthropic, '_build_client', lambda *args, **kwargs: (NS(messages=NS(stream=stream)), False))
    model = Model(id='claude-opus-4-6', name='test', provider='claude-code', api='anthropic-messages', base_url='https://api.anthropic.com', fast=True)

    async def run():
        return [event async for event in anthropic.stream_simple(model, Context(messages=[]), SimpleStreamOptions(api_key='test', service_tier=tier))]

    final = asyncio.run(run())[-1].message
    if tier == 'priority':
        assert params['extra_body'] == {'speed': 'fast'}
        assert extract_usage(final)['service_tiers'] == ['unreported']
    else:
        assert 'extra_body' not in params
        assert 'service_tiers' not in extract_usage(final)
