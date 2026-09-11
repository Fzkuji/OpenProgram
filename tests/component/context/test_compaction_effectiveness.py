"""Compaction must reduce the actual rendered prompt without omitting its input."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.context.engine import DefaultContextEngine
from openprogram.context.nodes import Call, ROLE_CODE, render_context
from openprogram.context.render import render_dag_messages
from openprogram.context.tokens import estimate_history_tokens
from openprogram.store.session.session_node_writer import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.agent.session_db.default_db', lambda: store)
    store.create_session('compact', 'main')
    prev = None
    for i in range(8):
        store.append_message('compact', {'id': f'u{i}', 'role': 'user',
            'content': f'Original requirement {i}', 'predecessor': prev})
        store.append_message('compact', {'id': f'a{i}', 'role': 'assistant',
            'content': f'Answer {i}', 'predecessor': f'u{i}'})
        SessionNodeWriter(store, 'compact', advance_head=False).append(Call(
            id=f't{i}', role=ROLE_CODE, name='todo_list', caller=f'a{i}',
            output=f'Important tool evidence {i}\n' + ('evidence payload ' * 1800),
        ))
        prev = f'a{i}'
    yield store
    store.close()


def rendered(store):
    g = SessionNodeWriter(store, 'compact').load()
    ids = render_context(g, head_id='a7', frame_entry_seq=-1)
    return g, ids, render_dag_messages(g, ids)


def test_short_tool_heavy_conversation_compacts_complete_input(conversation, monkeypatch):
    eng = DefaultContextEngine()
    captured = []

    async def summarize(**kwargs):
        captured.extend(kwargs['prefix'])
        return 'Original requirements and tool evidence retained in this recap.'

    monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize)
    _, _, before = rendered(conversation)
    result = asyncio.run(eng.compact(agent=None, session_id='compact', model=None,
                                    user_initiated=True))
    assert not result.no_op
    g, ids, after = rendered(conversation)
    covered = g.nodes[result.summary_id].metadata['covers_ids']
    supplied = '\n'.join(m['content'] for m in captured)
    assert 'Original requirement 0' in supplied
    assert 'Important tool evidence 0' in supplied
    assert result.summarised_count == len(covered)
    assert 'u6' in ids and 'a7' in ids
    assert estimate_history_tokens(after) < estimate_history_tokens(before)
    assert result.tokens_after < result.tokens_before
    assert conversation.get_session('compact')['head_id'] == 'a7'


@pytest.mark.parametrize('mode', ['long', 'failure', 'cancel', 'edit'])
def test_rejected_candidate_leaves_history_unchanged(conversation, monkeypatch, mode):
    import threading
    eng = DefaultContextEngine()
    cancel = threading.Event()
    before_ids = {m['id'] for m in conversation.get_messages('compact')}

    called = []

    async def summarize(**kwargs):
        called.append(True)
        if mode == 'failure':
            raise RuntimeError('provider unavailable')
        if mode == 'cancel':
            cancel.set()
        if mode == 'edit':
            conversation.update_node('compact', 't0', output='updated during summary')
        return 'enlarged summary ' * 300000 if mode == 'long' else 'recap'

    monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize)
    result = asyncio.run(eng.compact(agent=None, session_id='compact', model=None,
                                    cancel_event=cancel))
    assert called
    assert result.no_op
    assert {m['id'] for m in conversation.get_messages('compact')} == before_ids
    assert conversation.get_session('compact')['head_id'] == 'a7'


def test_nonreducing_summary_within_output_budget_is_not_persisted(conversation, monkeypatch):
    eng = DefaultContextEngine()
    for i in range(8):
        conversation.update_node('compact', f't{i}', output='brief tool result')
    called = []

    async def summarize(**kwargs):
        called.append(True)
        return 'Long but valid summary. ' * 200

    monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize)
    before = {m['id'] for m in conversation.get_messages('compact')}
    result = asyncio.run(eng.compact(agent=None, session_id='compact', model=None,
                                    keep_recent_tokens=0))
    assert called and result.no_op
    assert result.error == 'Summary does not reduce context; history unchanged'
    assert {m['id'] for m in conversation.get_messages('compact')} == before


def test_small_conversation_does_not_call_provider(conversation, monkeypatch):
    eng = DefaultContextEngine()
    for i in range(8):
        conversation.update_node('compact', f't{i}', output='brief result')

    async def unexpected(**kwargs):
        pytest.fail('small history should not call a summary model')

    monkeypatch.setattr(eng.summarizer, '_llm_summary', unexpected)
    assert asyncio.run(eng.compact(agent=None, session_id='compact', model=None)).no_op


def test_statistics_count_the_same_rendered_messages(conversation):
    from openprogram.context.session_stats import compute_breakdown
    _, _, messages = rendered(conversation)
    assert compute_breakdown('compact')['messages'] == estimate_history_tokens(messages)


def test_recompaction_absorbs_summary_and_counts_real_coverage(conversation, monkeypatch):
    eng = DefaultContextEngine()
    inputs = []

    async def summarize(**kwargs):
        inputs.append(kwargs)
        return f'Prior requirements retained; summary round {len(inputs)}.'

    monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize)
    first = asyncio.run(eng.compact(agent=None, session_id='compact', model=None))
    assert first.summary_id
    prev = 'a7'
    for i in range(8, 12):
        conversation.append_message('compact', {'id': f'u{i}', 'role': 'user',
            'content': f'New requirement {i}', 'predecessor': prev})
        conversation.append_message('compact', {'id': f'a{i}', 'role': 'assistant',
            'content': 'New answer', 'predecessor': f'u{i}'})
        SessionNodeWriter(conversation, 'compact', advance_head=False).append(Call(
            id=f't{i}', role=ROLE_CODE, name='todo_list', caller=f'a{i}',
            output='new tool evidence ' * 1800))
        prev = f'a{i}'
    second = asyncio.run(eng.compact(agent=None, session_id='compact', model=None))
    assert second.summary_id and second.used_previous_summary
    graph = SessionNodeWriter(conversation, 'compact').load()
    coverage = graph.nodes[second.summary_id].metadata['covers_ids']
    assert set(graph.nodes[first.summary_id].metadata['covers_ids']) < set(coverage)
    assert first.summary_id not in coverage
    assert second.summarised_count == len(coverage)
    assert 'summary round 1' in inputs[-1]['prefix'][0]['content']
    assert inputs[-1]['previous_summary'] is None
