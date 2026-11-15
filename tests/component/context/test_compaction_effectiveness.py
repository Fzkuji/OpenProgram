"""Compaction must reduce the actual rendered prompt without omitting its input."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.runtime.context.engine import DefaultContextEngine
from openprogram.state.store.session.nodes import Call, ROLE_CODE
from openprogram.runtime.context.nodes import render_context
from openprogram.runtime.context.render import render_dag_messages
from openprogram.runtime.context.tokens import estimate_history_tokens
from openprogram.runtime.context.compaction.summarize import Summarizer
from openprogram.state.store.session.session_node_writer import SessionNodeWriter
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
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


def test_cut_can_summarize_giant_completed_turn_before_current_request():
    history = []
    for i in range(4):
        history.extend([
            {'role': 'user', 'content': f'request {i}', '_context_tokens': 50},
            {'role': 'assistant', 'content': f'answer {i}', '_context_tokens': 1500},
        ])
    history.extend([
        {'role': 'user', 'content': 'run tools', '_context_tokens': 50},
        {'role': 'assistant', 'content': 'completed tool evidence', '_context_tokens': 60000},
        {'role': 'user', 'content': 'continue from the evidence', '_context_tokens': 30},
    ])

    cut = Summarizer().find_cut_index(history, context_window=1_000_000)
    assert cut == len(history) - 1
    assert history[cut]['content'] == 'continue from the evidence'


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
    events = []
    result = asyncio.run(eng.compact(agent=None, session_id='compact', model=None,
                                    cancel_event=cancel, on_event=events.append))
    assert called
    assert events[-1]["data"]["type"] == "compaction_failed"
    assert events[-1]["data"]["error"]
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
    from openprogram.runtime.context.session_stats import compute_breakdown
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


def test_multiple_assistant_messages_keep_two_user_turns(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
    store.create_session('multi', 'main')
    prev = None
    try:
        for i in range(3):
            store.append_message('multi', {'id': f'u{i}', 'role': 'user',
                'content': f'Requirement {i}', 'predecessor': prev})
            prev = f'u{i}'
            for j in range(3):
                nid = f'a{i}_{j}'
                store.append_message('multi', {'id': nid, 'role': 'assistant',
                    'content': 'Long response details ' * 1500, 'predecessor': prev})
                prev = nid
        eng = DefaultContextEngine()

        async def summarize(**kwargs):
            return 'Complete first-turn summary.'

        monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize)
        result = asyncio.run(eng.compact(agent=None, session_id='multi', model=None))
        assert result.summary_id
        graph = SessionNodeWriter(store, 'multi').load()
        ids = render_context(graph, head_id=prev, frame_entry_seq=-1)
        assert 'u1' in ids and 'u2' in ids
        assert all(f'a{i}_{j}' in ids for i in (1, 2) for j in range(3))
    finally:
        store.close()


def _install_summary(store, eng, monkeypatch, during=None):
    async def summarize(**kwargs):
        if during:
            during()
        return 'recap'

    monkeypatch.setattr(eng.summarizer, '_llm_summary', summarize)
    return asyncio.run(eng.compact(agent=None, session_id='compact', model=None))


def test_a_write_on_another_branch_keeps_the_paid_summary(conversation, monkeypatch):
    def side_write():
        SessionNodeWriter(conversation, 'compact', advance_head=False).append(Call(
            id='side', role='llm', name='side', predecessor='a3', output='on a side branch',
        ))

    result = _install_summary(conversation, DefaultContextEngine(), monkeypatch, side_write)
    assert result.summary_id and not result.no_op
    assert conversation.get_session('compact')['head_id'] == 'a7'


def test_a_change_to_a_covered_node_refuses_the_summary(conversation, monkeypatch):
    result = _install_summary(
        conversation, DefaultContextEngine(), monkeypatch,
        lambda: conversation.update_node('compact', 'a0', output='rewritten'),
    )
    assert result.no_op and 'changed' in result.error


def test_a_concurrent_summary_refuses_the_second(conversation, monkeypatch):
    def other_compaction():
        from openprogram.runtime.context.persistence import default_persister, rendered_history
        history = rendered_history(conversation, 'compact')
        assert default_persister.insert_summary_node(
            'compact', summary_text='first', cut_idx=4, history=history)

    result = _install_summary(conversation, DefaultContextEngine(), monkeypatch, other_compaction)
    assert result.no_op and 'changed' in result.error


def _reports(count, start=0):
    return [{'role': 'assistant', 'content': f'report {i}', 'always_on_report': f'r{i}',
             '_context_tokens': 3000} for i in range(start, start + count)]


def test_a_run_of_reports_before_the_next_message_can_be_folded():
    history = [
        {'role': 'user', 'content': 'watch this', '_context_tokens': 50},
        {'role': 'assistant', 'content': 'watching', '_context_tokens': 50},
        *_reports(10),
        {'role': 'user', 'content': 'what happened?', '_context_tokens': 30},
    ]
    summarizer = Summarizer()
    cut = summarizer.find_cut_index(history, keep_recent_tokens=9000)
    assert history[cut].get('always_on_report')
    assert sum(m['_context_tokens'] for m in history[cut:]) <= 9000
    # The first boundary whose tail fits, so nothing that fits is folded.
    assert sum(m['_context_tokens'] for m in history[cut - 1:]) > 9000


def test_reports_after_the_last_message_can_be_folded():
    history = [
        {'role': 'user', 'content': 'watch this', '_context_tokens': 50},
        {'role': 'assistant', 'content': 'watching', '_context_tokens': 50},
        *_reports(10),
    ]
    cut = Summarizer().find_cut_index(history, keep_recent_tokens=12000)
    assert cut == len(history) - 4


def test_a_conversation_without_reports_cuts_where_it_did():
    history = [
        {'role': 'user', 'content': 'only request', '_context_tokens': 50},
        *[{'role': 'assistant', 'content': f'step {i}', '_context_tokens': 3000} for i in range(10)],
    ]
    assert Summarizer().find_cut_index(history, keep_recent_tokens=9000) == 0


def test_reports_in_a_real_conversation_fold_into_a_summary(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: store)
    store.create_session('compact', 'main')
    store.append_message('compact', {'id': 'u0', 'role': 'user', 'content': 'watch this', 'predecessor': None})
    previous = 'u0'
    for i in range(10):
        store.append_message('compact', {
            'id': f'r{i}', 'role': 'assistant', 'content': f'report {i} ' + ('detail ' * 4000),
            'predecessor': previous, 'always_on_report': f'report{i}', 'status': 'done',
        })
        previous = f'r{i}'
    store.append_message('compact', {'id': 'u1', 'role': 'user', 'content': 'next', 'predecessor': previous})

    result = _install_summary(store, DefaultContextEngine(), monkeypatch)
    assert result.summary_id
    graph = SessionNodeWriter(store, 'compact').load()
    ids = render_context(graph, head_id='u1', frame_entry_seq=-1)
    assert result.summary_id in ids and 'u0' not in ids and 'r9' in ids and 'u1' in ids
    store.close()


def test_an_over_budget_turn_sends_no_snip_event(monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    prepared = []

    class Engine:
        def on_session_start(self, _session_id): ...
        def prepare(self, **kwargs):
            prepared.append(kwargs['history'])
            return SimpleNamespace(agent_messages=[], history_dicts=kwargs['history'], context_window=100)
        def should_auto_compact(self, _prep):
            return True
        async def compact(self, **_kwargs):
            return SimpleNamespace(summary_id=None)

    monkeypatch.setattr('openprogram.runtime.context.resolve_engine_for', lambda _agent: Engine())
    monkeypatch.setattr('openprogram.runtime.context.system_prompt_node.record_system_prompt', lambda *_a: None)
    db = SimpleNamespace(get_session=lambda _sid: {}, get_branch=lambda *_a: [],
                         merge_node_metadata=lambda *_a, **_k: None)
    req = SimpleNamespace(session_id='s', history_override=None, background=False, run_note=None, user_msg_id='u',
                          permission_mode=None)
    # Several whole turns, far over the window: the shape the old fallback cut.
    turns = [message for i in range(8) for message in (
        {'role': 'user', 'content': f'request {i}'}, {'role': 'assistant', 'content': f'answer {i}'})]
    events = []
    context = loop_runner._new_turn_agent_context(
        req, turns, events.append,
        db=db, agent_profile={}, model=None, tools=[], system_prompt='p',
        recordable_system_prompt='p', runtime_contract=None, assistant_msg_id=None,
    )
    assert context.messages == []
    assert events == []
    assert prepared == [turns]


def test_a_conversation_summary_marks_cancelled_requests_and_attributes_claims(monkeypatch):
    from tests.support.summary_stream import sectioned

    from openprogram.runtime.context.compaction import bounded_summary, prompts

    assert 'cancelled' in prompts.SUMMARY_RULES and 'the assistant said' in prompts.SUMMARY_RULES
    seen = {}

    async def capture(source, model, options, *, instruction, max_tokens, **kwargs):
        seen['instruction'] = instruction
        return sectioned()

    monkeypatch.setattr(bounded_summary, 'summarize_text', capture)
    text = asyncio.run(Summarizer()._llm_summary(
        prefix=[{'role': 'user', 'content': 'never mind, cancel that'}],
        model=None, previous_summary=None, cancel_event=None))
    assert text == sectioned() and prompts.SUMMARY_RULES in seen['instruction']
