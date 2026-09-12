"""Manual function admission resumes through the production spawned worker."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROGRAM = """
import asyncio, json, sys, time
from pathlib import Path
from openprogram import agentic_function
from openprogram.agentic_programming.continuation import step


def first(folder):
    path = Path(folder)
    with (path / 'effects').open('a') as stream:
        stream.write('first\\n')
    (path / 'entered').touch()
    deadline = time.monotonic() + 15
    while not (path / 'release').exists():
        if time.monotonic() > deadline:
            raise TimeoutError('pause was not delivered')
        time.sleep(0.01)
    return 'saved'


def finish(folder, value):
    with (Path(folder) / 'effects').open('a') as stream:
        stream.write(value + '\\n')
    return value


@agentic_function(name='durable_program', resumable=True)
def durable_program(folder: str):
    previous = step('first', first, folder)
    return step('finish', finish, folder, previous + '-VERSION')


async def main():
    from openprogram.agent.production_driver import CanonicalAgentAdapter
    from openprogram.agent.session_db import default_db
    from openprogram.execution import default_store, default_control_service
    from openprogram.agent.authority import local_owner_authority
    mode, folder, policy = sys.argv[1:]
    store = default_store()
    control = default_control_service()
    if mode == 'start':
        from openprogram.auth.store import get_store
        from openprogram.auth.types import Credential, CredentialData
        get_store().add_credential(Credential(provider_id='openai', account_id='default', kind='api_key', payload=CredentialData(kind='api_key', auth_value='isolated-test-placeholder', base_url='http://127.0.0.1:9/v1')))
        default_db().create_session('session', 'main', work_dir=folder)
        adapter = CanonicalAgentAdapter()
        admission = adapter.admit_payload(
            session_id='session', payload={'version': 1, 'kind': 'forced_tool', 'tool_name': 'durable_program', 'tool_input': {'folder': folder}, 'anchor_msg_id': 'pred:ROOT|node:function-node', 'work_dir': folder, 'source': 'fn-form', 'provider': 'openai', 'model': 'durable-test'},
            trusted_actor=local_owner_authority(), user_message_id='user', assistant_message_id='function-node', config_snapshot_ref='test',
        )
        (Path(folder) / 'execution-id').write_text(admission.execution_id)
        async def pause():
            deadline = time.monotonic() + 20
            while not (Path(folder) / 'entered').exists():
                if time.monotonic() > deadline:
                    raise TimeoutError(str(store.get_execution(admission.execution_id).to_dict()) + (Path(folder) / 'activation-result').read_text() if (Path(folder) / 'activation-result').exists() else 'no activation result')
                await asyncio.sleep(0.01)
            current = store.get_execution(admission.execution_id)
            await control.request_pause(command_id='pause', execution_id=current.execution_id, expected_version=current.status_version, actor={'surface': 'test'})
            (Path(folder) / 'release').touch()
        async def activate():
            result = await adapter.activate(admission)
            (Path(folder) / 'activation-result').write_text(repr(result))
        await asyncio.gather(activate(), pause())
        execution = store.get_execution(admission.execution_id)
    else:
        execution = store.get_execution((Path(folder) / 'execution-id').read_text())
        await control.request_continue(command_id='continue', execution_id=execution.execution_id, expected_version=execution.status_version, actor={'surface': 'test'}, code_change_policy=policy)
        deadline = time.monotonic() + 20
        while (execution := store.get_execution(execution.execution_id)).status.value in {'running', 'pausing'}:
            if time.monotonic() > deadline:
                raise TimeoutError(str(execution.to_dict()))
            await asyncio.sleep(0.01)
    nodes = [node for node in default_db().get_nodes('session') if node.is_code()]
    print(json.dumps({'status': execution.status.value, 'reason': execution.reason_code, 'nodes': [node.id for node in nodes]}))


if __name__ == '__main__':
    asyncio.run(main())
"""


@pytest.mark.parametrize("policy,suffix", [("keep_original", "A"), ("use_latest", "B")])
def test_manual_function_subprocess_resumes_same_node(tmp_path, policy, suffix):
    folder = tmp_path / "work"
    folder.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    state = home / ".openprogram"
    state.mkdir()
    (state / "config.json").write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "models": [
                            {
                                "id": "durable-test",
                                "name": "Durable test",
                                "api": "openai-completions",
                                "base_url": "http://127.0.0.1:9/v1",
                            }
                        ]
                    }
                }
            }
        )
    )
    program = tmp_path / "program.py"
    env = {
        **os.environ,
        "HOME": str(home),
        "OPENPROGRAM_PROFILE": "",
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
    }
    program.write_text(_PROGRAM.replace("VERSION", "A"))
    first_run = subprocess.run(
        [sys.executable, str(program), "start", str(folder), policy],
        env=env,
        text=True,
        capture_output=True,
        timeout=35,
    )
    assert first_run.returncode == 0, first_run.stderr
    first_result = json.loads(first_run.stdout.strip().splitlines()[-1])
    assert first_result["status"] == "paused", first_result
    assert (folder / "effects").read_text().splitlines() == ["first"]
    program.write_text(_PROGRAM.replace("VERSION", "B"))
    resumed = subprocess.run(
        [sys.executable, str(program), "resume", str(folder), policy],
        env=env,
        text=True,
        capture_output=True,
        timeout=35,
    )
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert result["status"] == "completed", result
    assert result["nodes"] == first_result["nodes"] == ["function-node"]
    assert (folder / "effects").read_text().splitlines() == ["first", "saved-" + suffix]
