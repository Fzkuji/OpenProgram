"""Which stored rows make up one turn of the transcript."""
from openprogram.interfaces.server.transcript.turn_index import turn_groups
from openprogram.interfaces.server.ws_actions.session.transcript import _rebuild_runtime_cards


def user(row_id, predecessor=None, **fields):
    return {'id': row_id, 'role': 'user', 'content': row_id, 'predecessor': predecessor, **fields}


def reply(row_id, predecessor, **fields):
    return {'id': row_id, 'role': 'assistant', 'content': row_id, 'predecessor': predecessor, **fields}


def transcript():
    """One of every row a turn can own, in display order, with call rows last."""
    return [
        user('u1'),
        reply('a1', 'u1', blocks=[{'type': 'text', 'text': 'x'}]),
        {'id': 'attach-1', 'role': 'assistant', 'function': 'attach', 'predecessor': 'a1', 'content': 'card'},
        {'id': 'attach-2', 'role': 'assistant', 'function': 'attach', 'predecessor': 'a1', 'caller': 'a1', 'content': 'card'},
        {'id': 'status-1', 'role': 'assistant', 'type': 'status', 'display': 'runtime', 'predecessor': 'a1', 'content': ''},
        user('u2', 'a1'),
        user('steer-1', 'u2', steering=True),
        reply('a2', 'steer-1'),
        user('u3', 'a2'),
        reply('cut', 'u3'),
        {'id': 'c1_ui', 'role': 'system', 'kind': 'compaction', 'slot': 'event', 'content': 'compacted'},
        user('restart-1', 'cut', trigger='restart'),
        reply('continued', 'restart-1'),
        reply('proactive-1', 'continued'),
        reply('proactive-2', 'proactive-1'),
        {'id': 'run-1', 'role': 'assistant', 'type': 'status', 'display': 'runtime', 'function': 'build', 'predecessor': '', 'content': ''},
        user('followup', 'run-1', job_followup=True),
        user('spawned', 'followup', caller='followup', spawn_branch_root=True),
        reply('a3', 'spawned'),
        {'id': 'tool-1', 'role': 'tool', 'function': 'read', 'caller': 'a1', 'content': 'tool'},
        {'id': 'leaf-1', 'role': 'assistant', 'caller': 'tool-1', 'content': 'leaf'},
    ]


def test_a_turn_is_a_prompt_and_everything_it_owns():
    assert [(key, [row['id'] for row in rows]) for key, rows in turn_groups(transcript())] == [
        ('u1', ['u1', 'a1', 'attach-1', 'attach-2', 'status-1', 'tool-1', 'leaf-1']),
        ('u2', ['u2', 'steer-1', 'a2']),
        ('u3', ['u3', 'cut', 'c1_ui', 'restart-1', 'continued']),
        ('proactive-1', ['proactive-1']),
        ('proactive-2', ['proactive-2']),
        ('run-1', ['run-1']),
        ('followup', ['followup']),
        ('spawned', ['spawned', 'a3']),
    ]


def test_only_openers_head_a_turn():
    keys = {key for key, _ in turn_groups(transcript())}
    assert not keys & {'attach-1', 'attach-2', 'status-1', 'steer-1', 'restart-1', 'cut', 'continued', 'c1_ui', 'tool-1', 'leaf-1'}


def test_a_compaction_row_before_the_first_opener_joins_that_turn_without_naming_it():
    rows = [{'id': 'c_card', 'role': 'system', 'kind': 'compaction', 'slot': 'card', 'covers_ids': ['x'], 'content': 's'}, user('u1'), reply('a1', 'u1')]
    assert [(key, [row['id'] for row in turn]) for key, turn in turn_groups(rows)] == [('u1', ['c_card', 'u1', 'a1'])]


def test_a_side_row_of_a_steer_before_the_first_opener_stays_in_the_first_turn():
    rows = [
        user('steer-0', steering=True),
        {'id': 'attach-1', 'role': 'assistant', 'function': 'attach', 'predecessor': 'steer-0', 'content': 'c'},
        user('u1', 'steer-0'), user('u2', 'u1'),
    ]
    assert [[row['id'] for row in turn] for _, turn in turn_groups(rows)] == [['steer-0', 'attach-1', 'u1'], ['u2']]


def test_a_system_row_without_a_prompt_opens_a_turn_and_one_after_a_prompt_stays():
    rows = [
        {'id': 's0', 'role': 'system', 'content': 'first'},
        user('u1', 's0'), {'id': 's1', 'role': 'system', 'predecessor': 'u1', 'content': 'in turn'},
        {'id': 's2', 'role': 'system', 'content': 'on its own'},
    ]
    assert [key for key, _ in turn_groups(rows)] == ['s0', 'u1', 's2']


def test_a_long_always_on_chain_stays_one_row_per_turn():
    rows = [reply('a0', None), *[reply(f'a{i}', f'a{i - 1}') for i in range(1, 100)]]
    assert [turn for _, turn in turn_groups(rows)] == [[row] for row in rows]


def rebuilt_runs(monkeypatch, count, first_predecessor=None):
    """Stored top-level function runs, turned into cards by the real rebuild."""
    from openprogram.runtime.execution import dag
    monkeypatch.setattr(dag, 'build_exec_dag_by_id', lambda session_id, node_id: {'id': node_id})
    rows = [
        {'id': f'run{i}', 'role': 'tool', 'function': 'build', 'content': '',
         'predecessor': f'run{i - 1}' if i else first_predecessor}
        for i in range(count)
    ]
    return _rebuild_runtime_cards(rows, rows, 's')


def test_consecutive_function_runs_each_open_a_turn(monkeypatch):
    cards = rebuilt_runs(monkeypatch, 3, 'a1')
    assert all(card['type'] == 'status' and card['display'] == 'runtime' and card['predecessor'] == '' for card in cards)
    rows = [user('u1'), reply('a1', 'u1'), *cards, user('u2', 'run2')]
    assert [(key, [row['id'] for row in turn]) for key, turn in turn_groups(rows)] == [
        ('u1', ['u1', 'a1']), ('run0', ['run0']), ('run1', ['run1']), ('run2', ['run2']), ('u2', ['u2']),
    ]


def test_a_runtime_placeholder_stays_with_the_reply_it_hangs_from():
    placeholder = {'id': 'p1', 'role': 'assistant', 'type': 'status', 'display': 'runtime', 'predecessor': 'a1', 'content': ''}
    rows = [user('u1'), reply('a1', 'u1'), placeholder, user('u2', 'a1')]
    assert [[row['id'] for row in turn] for _, turn in turn_groups(rows)] == [['u1', 'a1', 'p1'], ['u2']]


def test_a_side_row_whose_parent_is_not_shown_stays_at_its_display_position():
    rows = [user('u1'), {'id': 'attach-1', 'role': 'assistant', 'function': 'attach', 'predecessor': 'gone', 'content': 'c'}, user('u2', 'u1')]
    assert [[row['id'] for row in turn] for _, turn in turn_groups(rows)] == [['u1', 'attach-1'], ['u2']]
