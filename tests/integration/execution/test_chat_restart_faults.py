"""Kill a test-owned worker at durable boundaries, then recover in a new process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.support.waiting import wait_until


PROGRAM = r'''
import asyncio, json, sys, threading
from pathlib import Path
from types import SimpleNamespace
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
from openprogram.runtime.context.session_db import default_db
from openprogram.runtime.permissions.authority import local_owner_authority
from openprogram.runtime.execution.run_control import get_current_execution_id
from openprogram.runtime.execution import default_store, default_control_service
from openprogram.runtime.execution.attempts import AttemptStore, AttemptConflict
from openprogram.runtime.execution.effects import EffectStore, EffectClassification, EffectStatus
from openprogram.runtime.execution.restart import reconcile
from openprogram.programs.tools.planning.goal import chat
import openprogram.programs.tools.planning.goal as goals
from tests.support.waiting import wait_until

mode, folder, phase, has_goal = sys.argv[1:]
folder = Path(folder)
has_goal = has_goal == 'yes'
store, control = default_store(), default_control_service()
effects = EffectStore(store)
ready = folder / 'ready.json'
if mode == 'start':
    default_db().create_session('fault-chat', 'main', work_dir=str(folder))
    if has_goal:
        chat.create('fault-chat', 'Finish the saved work')
    def work(*, request, cancel_event):
        execution = store.get_execution(get_current_execution_id())
        attempt = AttemptStore(store).get(execution.current_attempt_id)
        effect = effects.register(effect_id='operation', execution_id=execution.execution_id,
            attempt_id=attempt.attempt_id, action_id='once', classification=EffectClassification.NONREPEATABLE,
            idempotency_key=None, metadata={'kind': 'tool.before', 'payload': {'tool_name': 'write'}})
        if phase != 'before_dispatch':
            effects.mark_dispatched(effect.effect_id, expected_status=EffectStatus.PLANNED)
            with (folder / 'side-effects').open('a') as stream:
                stream.write('once\n')
        # Publish readiness only after the complete recovery identity is closed.
        # The parent intentionally kills this process as soon as ready exists.
        pending = ready.with_suffix('.tmp')
        pending.write_text(json.dumps({'execution_id': execution.execution_id,
                                      'attempt_id': attempt.attempt_id, 'generation': attempt.generation}))
        pending.replace(ready)
        if threading.Event().wait(25):
            raise AssertionError('unexpected signal')
        raise TimeoutError('test did not kill its worker')
    adapter = CanonicalAgentAdapter(turn_runner=work)
    admission = adapter.admit(TurnRequest('fault-chat', 'Do the work', 'main', 'web'),
        trusted_actor=local_owner_authority(), user_message_id='original-user', config_snapshot_ref='fault-test')
    asyncio.run(adapter.activate(admission))
else:
    saved = json.loads(ready.read_text())
    calls, ended = [], threading.Event()
    def recovered(*, request, cancel_event):
        calls.append(request)
        if has_goal:
            goals.apply_goal_action(request.session_id, 'pause')
        return SimpleNamespace(failed=False)
    activate = CanonicalAgentAdapter.activate
    async def tracked(self, admission, **kwargs):
        try:
            return await activate(self, admission, **kwargs)
        finally:
            ended.set()
    CanonicalAgentAdapter.activate = tracked
    import openprogram.runtime.conversation.production_driver as drivers
    drivers.CanonicalAgentAdapter = lambda **kw: CanonicalAgentAdapter(turn_runner=recovered, **kw)
    resumed_adapter = CanonicalAgentAdapter(turn_runner=recovered)
    async def activate_saved(attempt, activation):
        try:
            return await resumed_adapter.driver.activate(attempt, activation)
        finally:
            ended.set()
    control.activator = activate_saved
    control.recover_startup()
    runner = SimpleNamespace(_execution_store=store, _execution_control=control)
    reconcile(runner)
    assert wait_until(lambda: calls and all(e.status.value in {'completed', 'cancelled', 'interrupted'}
        for e in store.list_for_session('fault-chat')), timeout=10), [e.to_dict() for e in store.list_for_session('fault-chat')]
    control.recover_startup()
    reconcile(runner)
    assert len(calls) == 1, calls
    executions = store.list_for_session('fault-chat')
    assert len(executions) == (1 if phase == 'before_dispatch' else 2)
    latest = executions[0] if phase == 'before_dispatch' else next(e for e in executions if e.execution_id != saved['execution_id'])
    assert store.get_execution_input(latest.execution_id).trusted_actor == store.get_execution_input(saved['execution_id']).trusted_actor
    assert calls[0].history_override is None
    assert ('Do the work' if phase == 'before_dispatch' else 'Inspect actual state') in calls[0].user_text
    try:
        AttemptStore(store).ensure_held(saved['attempt_id'], generation=saved['generation'])
    except AttemptConflict as error:
        assert error.code == 'stale_owner', error.code
    else:
        raise AssertionError('old owner accepted after recovery')
    effect = effects.get('operation')
    if phase == 'after_start':
        assert effect.status is EffectStatus.DISPATCHED
    else:
        assert effect.status is EffectStatus.PLANNED
    print(json.dumps({'executions': len(executions), 'continuations': len(calls), 'effect': effect.status.value}))
'''


@pytest.mark.parametrize("has_goal", [False, True])
@pytest.mark.parametrize("phase", ["before_dispatch", "after_start"])
def test_killed_chat_recovers_once_without_repeating_effects(tmp_path, has_goal, phase):
    home = tmp_path / "home"
    home.mkdir()
    program = tmp_path / "worker.py"
    program.write_text(PROGRAM)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "OPENPROGRAM_PROFILE": "",
           "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    arguments = [str(program), "start", str(tmp_path), phase, "yes" if has_goal else "no"]
    worker = subprocess.Popen([sys.executable, *arguments], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_until(lambda: (tmp_path / "ready.json").exists() or worker.poll() is not None, timeout=15)
        assert worker.poll() is None, worker.communicate(timeout=2)
        worker.kill()
        worker.communicate(timeout=5)
        arguments[1] = "recover"
        recovered = subprocess.run([sys.executable, *arguments], env=env, capture_output=True, text=True, timeout=25)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        assert json.loads(recovered.stdout.strip().splitlines()[-1])["continuations"] == 1
        effects = tmp_path / "side-effects"
        assert (effects.read_text().splitlines() if effects.exists() else []) == ([] if phase == "before_dispatch" else ["once"])
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)


def test_readiness_waits_for_complete_recovery_identity(tmp_path, monkeypatch):
    slow_write = r'''
original_write = Path.write_text
def slow_ready_write(path, *args, **kwargs):
    if path.name in {'ready.json', 'ready.tmp'}:
        path.touch()
        threading.Event().wait(0.2)
    return original_write(path, *args, **kwargs)
Path.write_text = slow_ready_write
'''
    monkeypatch.setattr(sys.modules[__name__], 'PROGRAM', PROGRAM.replace(
        'mode, folder, phase, has_goal = sys.argv[1:]',
        slow_write + '\nmode, folder, phase, has_goal = sys.argv[1:]',
    ))
    test_killed_chat_recovers_once_without_repeating_effects(tmp_path, True, 'before_dispatch')
