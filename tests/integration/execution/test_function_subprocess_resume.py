"""Manual function admission resumes through the production spawned worker."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROGRAM = "\nimport asyncio, json, sys, time\nfrom pathlib import Path\nfrom openprogram import Agent\nfrom openprogram.runtime.agent.durable_steps import step\n\n\ndef first(folder):\n    path = Path(folder)\n    with (path / 'effects').open('a') as stream:\n        stream.write('first\\n')\n    (path / 'entered').touch()\n    deadline = time.monotonic() + 15\n    while not (path / 'release').exists():\n        if time.monotonic() > deadline:\n            raise TimeoutError('pause was not delivered')\n        time.sleep(0.01)\n    return 'saved'\n\n\ndef finish(folder, value):\n    with (Path(folder) / 'effects').open('a') as stream:\n        stream.write(value + '\\n')\n    return value\n\n\nclass DurableProgramAgent(Agent):\n    method_options = {\n        'durable_program': {\n            'name': 'durable_program',\n            'resumable': True,\n            'tool': True\n        },\n    }\n\n    @staticmethod\n    def durable_program(folder: str):\n        previous = step('first', first, folder)\n        return step('finish', finish, folder, previous + '-VERSION')\n\n\ndurable_program = DurableProgramAgent().durable_program\n\n\nasync def main():\n    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter\n    from openprogram.runtime.context.session_db import default_db\n    from openprogram.runtime.execution import default_store, default_control_service\n    from openprogram.runtime.permissions.authority import local_owner_authority\n    mode, folder, policy = sys.argv[1:]\n    store = default_store()\n    control = default_control_service()\n    if mode == 'start':\n        from openprogram.models.auth.store import get_store\n        from openprogram.models.auth.types import Credential, CredentialData\n        get_store().add_credential(Credential(provider_id='openai', account_id='default', kind='api_key', payload=CredentialData(kind='api_key', auth_value='isolated-test-placeholder', base_url='http://127.0.0.1:9/v1')))\n        default_db().create_session('session', 'main', work_dir=folder)\n        adapter = CanonicalAgentAdapter()\n        admission = adapter.admit_payload(\n            session_id='session', payload={'version': 1, 'kind': 'forced_tool', 'tool_name': 'durable_program', 'tool_input': {'folder': folder}, 'anchor_msg_id': 'pred:ROOT|node:function-node', 'work_dir': folder, 'source': 'fn-form', 'provider': 'openai', 'model': 'durable-test'},\n            trusted_actor=local_owner_authority(), user_message_id='user', assistant_message_id='function-node', config_snapshot_ref='test',\n        )\n        (Path(folder) / 'execution-id').write_text(admission.execution_id)\n        async def pause():\n            deadline = time.monotonic() + 20\n            while not (Path(folder) / 'entered').exists():\n                if time.monotonic() > deadline:\n                    raise TimeoutError(str(store.get_execution(admission.execution_id).to_dict()) + (Path(folder) / 'activation-result').read_text() if (Path(folder) / 'activation-result').exists() else 'no activation result')\n                await asyncio.sleep(0.01)\n            current = store.get_execution(admission.execution_id)\n            await control.request_pause(command_id='pause', execution_id=current.execution_id, expected_version=current.status_version, actor={'surface': 'test'})\n            (Path(folder) / 'release').touch()\n        async def activate():\n            result = await adapter.activate(admission)\n            (Path(folder) / 'activation-result').write_text(repr(result))\n        await asyncio.gather(activate(), pause())\n        execution = store.get_execution(admission.execution_id)\n    else:\n        execution = store.get_execution((Path(folder) / 'execution-id').read_text())\n        await control.request_continue(command_id='continue', execution_id=execution.execution_id, expected_version=execution.status_version, actor={'surface': 'test'}, code_change_policy=policy)\n        deadline = time.monotonic() + 20\n        while (execution := store.get_execution(execution.execution_id)).status.value in {'running', 'pausing'}:\n            if time.monotonic() > deadline:\n                raise TimeoutError(str(execution.to_dict()))\n            await asyncio.sleep(0.01)\n    nodes = [node for node in default_db().get_nodes('session') if node.is_code()]\n    print(json.dumps({'status': execution.status.value, 'reason': execution.reason_code, 'nodes': [node.id for node in nodes]}))\n\n\nif __name__ == '__main__':\n    asyncio.run(main())\n"


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
