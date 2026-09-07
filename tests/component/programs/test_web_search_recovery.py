"""Search recovery through the public executor and real controlled pools."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import importlib

import pytest

from openprogram.programs._providers import ProviderRegistry
from openprogram.programs.tools.web.web_search import combine
from openprogram.programs.tools.web.web_search.registry import SearchResult

search = importlib.import_module('openprogram.programs.tools.web.web_search.web_search')


class Backend:
    priority = 100
    requires_env = ()

    def __init__(self, name, run, available=True):
        self.name, self.run, self.available = name, run, available

    def is_available(self):
        return self.available

    def search(self, query, *, num_results):
        return self.run()


@pytest.fixture
def registry(monkeypatch):
    value = ProviderRegistry('web_search')
    monkeypatch.setattr(combine, 'registry', value)
    monkeypatch.setattr(search, 'registry', value)
    monkeypatch.setattr('openprogram.setup.read_search_default_provider', lambda: None)
    return value


@pytest.mark.parametrize('strategy', ['race', 'rrf'])
def test_returns_timely_success_without_waiting_for_slow_provider(registry, strategy):
    entered, release, done = Event(), Event(), Event()

    def slow():
        entered.set()
        try:
            assert release.wait(3)
            return []
        finally:
            done.set()

    def fast():
        assert entered.wait(3)
        return [SearchResult('fast', 'https://example.com/fast')]

    registry.register(Backend('slow', slow))
    registry.register(Backend('fast', fast))
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(getattr(combine, 'combine_' + strategy), 'query', timeout=.05)
        try:
            results, contributors = future.result(timeout=.8)
            assert results[0].title == 'fast'
            assert 'fast' in contributors
            assert not done.is_set()
        finally:
            release.set()
            assert done.wait(3)


@pytest.mark.parametrize('strategy', ['race', 'rrf'])
def test_all_failed_is_an_error_not_empty_results(registry, strategy):
    def fail():
        raise RuntimeError('provider unavailable')
    registry.register(Backend('failed', fail))
    assert search.execute(query='query', combine=strategy).startswith('Error:')


@pytest.mark.parametrize('strategy', ['race', 'rrf'])
def test_successful_empty_response_is_not_an_error(registry, strategy):
    registry.register(Backend('empty', lambda: []))
    assert search.execute(query='query', combine=strategy).startswith('No results')


def test_saved_unavailable_default_falls_back_but_explicit_choice_does_not(registry, monkeypatch):
    registry.register(Backend('saved', lambda: [], available=False))
    registry.register(Backend('working', lambda: [SearchResult('found', 'https://example.com')]))
    monkeypatch.setattr('openprogram.setup.read_search_default_provider', lambda: 'saved')
    assert 'found' in search.execute(query='query')
    assert search.execute(query='query', provider='saved').startswith('Error:')


def test_invalid_strategy_is_rejected(registry):
    registry.register(Backend('working', lambda: []))
    assert search.execute(query='query', combine='wrong').startswith('Error:')


def test_jina_requires_key_and_schema_accepts_hot_credentials(monkeypatch):
    from openprogram.programs.tools.web.web_search.providers.jina import JinaProvider
    monkeypatch.delenv('JINA_API_KEY', raising=False)
    backend = JinaProvider()
    assert not backend.is_available()
    assert 'jina' in search.SPEC['parameters']['properties']['provider']['enum']
    monkeypatch.setenv('JINA_API_KEY', 'test-only')
    assert backend.is_available()
    assert 'jina' in search.SPEC['parameters']['properties']['provider']['enum']


def test_search_probe_does_not_block_event_loop(monkeypatch):
    import asyncio

    async def run():
        import asyncio
        from fastapi import FastAPI
        from openprogram.webui.routes.providers import register
        from openprogram.programs.tools.web.web_search.registry import registry as live_registry
        progressed = Event()
        loop = asyncio.get_running_loop()

        def search_probe():
            loop.call_soon_threadsafe(progressed.set)
            assert progressed.wait(1), 'synchronous search blocked the event loop'
            return []

        monkeypatch.setitem(live_registry._providers, 'probe', Backend('probe', search_probe))
        app = FastAPI()
        register(app)
        endpoint = next(r.endpoint for r in app.routes if r.path == '/api/search-providers/{provider_id}/test')
        response = await endpoint('probe', None)
        import json
        assert json.loads(response.body)['ok'] is True

    asyncio.run(run())
