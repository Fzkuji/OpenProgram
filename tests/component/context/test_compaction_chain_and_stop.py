"""A compaction works on the chain its caller names, writes its node once,
and stops when its turn is stopped.

- The chain is the caller's: compacting a side branch's tip reads and
  covers that branch, and the session head moving elsewhere does not make
  the frozen view stale (context/compaction.html, "Which chain").
- One rule says which summary is in effect, and the rendered request and
  the compaction input agree on it.
- The summary node is complete when written: no metadata stamped after.
- A summary request reads its stream under an idle limit and a stop
  signal: a set signal ends it at once, a steady stream may run long, a
  stalled one fails.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from openprogram.models.providers.types import AssistantMessage, Model, SimpleStreamOptions, TextContent
from openprogram.runtime.context.compaction.bounded_summary import summary_reply
from openprogram.runtime.context.compaction.view import load_compaction_view
from openprogram.runtime.context.engine import DefaultContextEngine
from openprogram.runtime.context.nodes import active_summary_for_chain, render_context
from openprogram.runtime.context.persistence import Persister, rendered_history
from openprogram.state.store.session.session_node_writer import SessionNodeWriter
from openprogram.state.store.session.session_store import SessionStore


def _model() -> Model:
    return Model(id='summary-fixture', name='fixture', api='openai-completions',
                 provider='openai', base_url='https://example.invalid',
                 context_window=200_000, max_tokens=8_000)


def _reply(text: str) -> AssistantMessage:
    m = _model()
    return AssistantMessage(content=[TextContent(text=text)], api=m.api,
                            provider=m.provider, model=m.id, timestamp=0)


def _turn(store, sid, tag, prev, size=400):
    store.append_message(sid, {'id': f'u{tag}', 'role': 'user',
                               'content': f'request {tag} ' + 'detail ' * size, 'predecessor': prev})
    store.append_message(sid, {'id': f'a{tag}', 'role': 'assistant',
                               'content': f'answer {tag} ' + 'finding ' * size,
                               'predecessor': f'u{tag}', 'status': 'done'})
    return f'a{tag}'


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Main branch a0..a5; a side branch s2..s5 forks after a1; head on a5."""
    store = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
    store.create_session('c', 'main')
    prev = None
    for i in range(6):
        prev = _turn(store, 'c', i, prev)
    side = 'a1'
    for i in range(2, 6):
        side = _turn(store, 'c', f's{i}', side)
    store.set_head('c', 'a5')
    yield store
    store.close()


def _engine(monkeypatch, summarize=None):
    eng = DefaultContextEngine()

    async def recap(**_kwargs):
        return 'recap of the older turns'

    monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize or recap)
    monkeypatch.setattr(eng, '_occupancy_tokens', lambda *_a, **_k: 50_000)
    return eng


def _chain(store, tip):
    return [m['id'] for m in store.get_branch('c', tip)]


def test_compacting_a_side_branch_covers_only_that_branch(store, monkeypatch):
    eng = _engine(monkeypatch)
    result = asyncio.run(eng.compact(agent=None, session_id='c', model=None,
                                     user_initiated=True, keep_recent_tokens=10,
                                     head_id='as5'))
    assert result.summary_id and not result.no_op
    graph = SessionNodeWriter(store, 'c').load()
    covered = set(graph.nodes[result.summary_id].metadata['covers_ids'])
    side = set(_chain(store, 'as5'))
    assert covered <= side
    assert covered & {'us2', 'as2'}, 'the side branch own turns are summarised'
    assert not covered & {'u2', 'a2', 'u3', 'a3', 'u4', 'a4'}
    # The side branch reads the summary; the main branch reads raw.
    assert result.summary_id in render_context(graph, head_id='as5', frame_entry_seq=-1)
    assert result.summary_id not in render_context(graph, head_id='a5', frame_entry_seq=-1)
    assert store.get_session('c')['head_id'] == 'a5'


def test_a_moved_session_head_leaves_the_view_of_a_chain_valid(store):
    view = load_compaction_view(store, 'c', 'a3')
    assert view.head_id == 'a3'
    store.set_head('c', 'as5')
    _turn(store, 'c', 'x', 'as5', size=4)
    assert view.unchanged(store, 'c')
    store.update_node('c', 'a2', output='rewritten')
    assert not view.unchanged(store, 'c')


def test_one_rule_names_the_summary_in_effect_everywhere(store):
    main = store.get_messages_by_id('c', _chain(store, 'a5'))
    first = Persister().insert_summary_node('c', summary_text='first recap', cut_idx=4, history=main)
    rolled = rendered_history(store, 'c', 'a5')
    second = Persister().insert_summary_node('c', summary_text='second recap', cut_idx=3, history=rolled)
    assert first and second

    graph = SessionNodeWriter(store, 'c').load()
    in_effect = active_summary_for_chain(graph, _chain(store, 'a5'))
    assert in_effect.id == second
    ids = render_context(graph, head_id='a5', frame_entry_seq=-1)
    assert second in ids and first not in ids
    view = rendered_history(store, 'c', 'a5')
    assert view[0]['id'] == second
    assert load_compaction_view(store, 'c', 'a5').history[0]['id'] == second

    # The side branch holds only the first summary's turns: it reads that
    # one, in both, and never the rolled summary written on the main chain.
    assert active_summary_for_chain(graph, _chain(store, 'as5')).id == first
    side = render_context(graph, head_id='as5', frame_entry_seq=-1)
    assert side[0] == first and second not in side
    assert [m['id'] for m in rendered_history(store, 'c', 'as5')] == side


def test_the_summary_node_is_written_once_with_its_facts(store, monkeypatch):
    eng = _engine(monkeypatch)
    monkeypatch.setattr(store, 'merge_node_metadata',
                        lambda *_a, **_k: pytest.fail('a summary node is not stamped after it is written'))
    result = asyncio.run(eng.compact(agent=None, session_id='c', model=_model(),
                                     keep_recent_tokens=10, head_id='a5'))
    assert result.summary_id
    meta = SessionNodeWriter(store, 'c').load().nodes[result.summary_id].metadata
    assert meta['trigger'] == 'auto'
    assert meta['model'] == 'openai/summary-fixture'
    assert meta['tokens_before'] == result.tokens_before == 50_000
    assert meta['tokens_after'] == result.tokens_after < 50_000
    assert meta['summarised_count'] == result.summarised_count == len(meta['covers_ids'])
    assert meta['compacted_at'] > 0
    assert meta['render_role'] == 'user'


def _slow_stream(pause: float, events: int, text: str = 'recap'):
    async def stream(model, context, options=None):
        for _ in range(events):
            await asyncio.sleep(pause)
            yield {'type': 'text_delta', 'delta': '.'}
        yield {'type': 'done', 'message': _reply(text)}
    return stream


def test_a_stop_ends_the_summary_request_and_writes_nothing(store, monkeypatch):
    monkeypatch.setattr('openprogram.models.providers.stream_simple', _slow_stream(0.1, 100))
    eng = DefaultContextEngine()
    monkeypatch.setattr(eng, '_occupancy_tokens', lambda *_a, **_k: 50_000)
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    events = []
    started = time.monotonic()
    result = asyncio.run(eng.compact(agent=None, session_id='c', model=_model(),
                                     keep_recent_tokens=10, head_id='a5',
                                     cancel_event=cancel, on_event=events.append))
    assert time.monotonic() - started < 1.5
    assert result.no_op and 'cancelled' in result.error
    assert not any(m.get('covers_ids') for m in store.get_messages('c'))
    assert events[-1]['data']['type'] == 'compaction_failed'


def test_a_steady_stream_outlives_the_idle_limit(monkeypatch):
    monkeypatch.setattr('openprogram.models.providers.stream_simple', _slow_stream(0.05, 12, 'long recap'))
    reply = asyncio.run(summary_reply(_model(), None, SimpleStreamOptions(), idle_s=0.2))
    assert reply.content[0].text == 'long recap'


def test_a_stalled_stream_fails_after_the_idle_limit(monkeypatch):
    monkeypatch.setattr('openprogram.models.providers.stream_simple', _slow_stream(5, 1))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(summary_reply(_model(), None, SimpleStreamOptions(), idle_s=0.2))
    assert time.monotonic() - started < 1.5


def test_pre_turn_compaction_runs_on_the_turn_chain_with_its_stop(monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    seen = {}

    class Engine:
        def should_auto_compact(self, _prep):
            return True

        async def compact(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(summary_id=None)

    cancel = threading.Event()
    req = SimpleNamespace(session_id='s', history_override=None, background=False)
    loop_runner._auto_compact_new_turn(
        Engine(), req, db=None, on_event=lambda _e: None, agent_profile={}, model=None,
        tools=[], system_prompt='p', session={}, history=[], prep=None,
        head_id='placeholder', cancel_event=cancel,
    )
    assert seen['head_id'] == 'placeholder'
    assert seen['cancel_event'] is cancel
