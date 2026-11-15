"""A resumable workflow entry resumes in a new process without repeating steps."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading

import pytest


_WORKFLOW = '''
"""Durable probe workflow."""
import time
from pathlib import Path

from openprogram import Agent, Context
from openprogram.runtime.agent.durable_steps import step

writer = Agent(instructions="You name receipts.", tools=[])


def durable_probe(folder: str) -> str:
    """Write two receipts, pausing between them."""
    first = step("first", write_first, folder)
    return step("finish", finish, folder, first + "-VERSION")


def write_first(folder):
    path = Path(folder)
    with (path / "effects").open("a") as stream:
        stream.write("first\\n")
    (path / "entered").touch()
    deadline = time.monotonic() + 15
    while not (path / "release").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("pause was not delivered")
        time.sleep(0.01)
    return "saved"


def finish(folder, value):
    """Name the receipt."""
    named = writer("Name it", context=Context({"value": value}))
    with (Path(folder) / "effects").open("a") as stream:
        stream.write(value + " " + named + "\\n")
    return value


PROGRAM_ENTRIES = [durable_probe]
ENTRY_OPTIONS = {"durable_probe": {"resumable": True}}
'''

_DRIVER = '''
import asyncio, json, os, sys, time
from pathlib import Path


async def main():
    from openprogram.programs._host.packages import load_user_packages
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution import default_store, default_control_service
    from openprogram.runtime.permissions.authority import local_owner_authority
    mode, folder, policy = sys.argv[1:]
    status = load_user_packages()["wf_durable_probe"]
    assert status.state == "loaded", status
    store = default_store()
    control = default_control_service()
    await run(mode, folder, policy, store, control)


async def run(mode, folder, policy, store, control):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.permissions.authority import local_owner_authority
    if mode == "start":
        from openprogram.models.auth.store import get_store
        from openprogram.models.auth.types import Credential, CredentialData
        get_store().add_credential(Credential(provider_id="openai", account_id="default", kind="api_key",
            payload=CredentialData(kind="api_key", auth_value="isolated-test-placeholder", base_url=os.environ["FAKE_MODEL_URL"])))
        default_db().create_session("session", "main", work_dir=folder)
        adapter = CanonicalAgentAdapter()
        admission = adapter.admit_payload(
            session_id="session",
            payload={"version": 1, "kind": "forced_tool", "tool_name": "durable_probe",
                     "tool_input": {"folder": folder}, "anchor_msg_id": "pred:ROOT|node:function-node",
                     "work_dir": folder, "source": "fn-form", "provider": "openai", "model": "durable-test"},
            trusted_actor=local_owner_authority(), user_message_id="user",
            assistant_message_id="function-node", config_snapshot_ref="test",
        )
        (Path(folder) / "execution-id").write_text(admission.execution_id)

        async def pause():
            deadline = time.monotonic() + 20
            while not (Path(folder) / "entered").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError(str(store.get_execution(admission.execution_id).to_dict()))
                await asyncio.sleep(0.01)
            current = store.get_execution(admission.execution_id)
            await control.request_pause(command_id="pause", execution_id=current.execution_id,
                                        expected_version=current.status_version, actor={"surface": "test"})
            (Path(folder) / "release").touch()

        await asyncio.gather(adapter.activate(admission), pause())
        execution = store.get_execution(admission.execution_id)
    else:
        execution = store.get_execution((Path(folder) / "execution-id").read_text())
        await control.request_continue(command_id="continue-" + policy, execution_id=execution.execution_id,
                                       expected_version=execution.status_version, actor={"surface": "test"},
                                       code_change_policy=policy)
        deadline = time.monotonic() + 20
        while (execution := store.get_execution(execution.execution_id)).status.value in {"running", "pausing"}:
            if time.monotonic() > deadline:
                raise TimeoutError(str(execution.to_dict()))
            await asyncio.sleep(0.01)
    log = store.function_log(execution.execution_id)
    log.nodes("")  # read what the subprocess appended
    incompatible = [value.get("reason", "") for (kind, _key), value in log._saved.items()
                    if kind == "function.incompatible"]
    print(json.dumps({"status": execution.status.value, "incompatible": incompatible}))


if __name__ == "__main__":
    asyncio.run(main())
'''

_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _commit(package, version):
    (package / "workflow.py").write_text(_WORKFLOW.replace("VERSION", version))
    env = {**os.environ, **_GIT_ENV}
    subprocess.run(["git", "-C", str(package), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(package), "commit", "-qm", version], check=True, env=env)
    subprocess.run(["git", "-C", str(package), "update-ref", "refs/openprogram/checked", "HEAD"], check=True, env=env)


@pytest.fixture
def fake_model():
    """An OpenAI-compatible endpoint that streams the reply "named"."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "named"},
                              "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            payload = "".join(
                "data: " + json.dumps({"id": "c", "object": "chat.completion.chunk", "created": 0,
                                       "model": "durable-test", **chunk}) + "\n\n"
                for chunk in chunks
            ) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload.encode())))
            self.end_headers()
            self.wfile.write(payload.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()


def _run(driver, mode, folder, policy, env):
    done = subprocess.run([sys.executable, str(driver), mode, str(folder), policy],
                          env=env, text=True, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("change,policy,expected", [
    (False, "keep_original", "A"),
    (True, "use_latest", "B"),
    (True, "keep_original", None),
])
def test_workflow_entry_resumes_from_its_last_step(tmp_path, fake_model, change, policy, expected):
    model_url, requests = fake_model
    folder = tmp_path / "work"
    folder.mkdir()
    home = tmp_path / "home"
    state = home / ".openprogram"
    package = state / "programs" / "wf_durable_probe"
    package.mkdir(parents=True)
    (state / "config.json").write_text(json.dumps({"providers": {"openai": {"models": [{
        "id": "durable-test", "name": "Durable test", "api": "openai-completions",
        "base_url": model_url}]}}}))
    (package / "__init__.py").write_text(
        textwrap.dedent('"""Durable probe."""\nfrom .workflow import ENTRY_OPTIONS, PROGRAM_ENTRIES\n'))
    subprocess.run(["git", "-C", str(package), "init", "-q"], check=True)
    _commit(package, "A")
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    env = {**os.environ, "HOME": str(home), "OPENPROGRAM_PROFILE": "",
           "PYTHONPATH": str(Path(__file__).resolve().parents[3]), "FAKE_MODEL_URL": model_url}

    first = _run(driver, "start", folder, policy, env)
    assert first["status"] == "paused", first
    assert (folder / "effects").read_text().splitlines() == ["first"]
    assert requests == []

    if change:
        _commit(package, "B")
    resumed = _run(driver, "resume", folder, policy, env)
    if expected is None:
        assert resumed["status"] == "paused", resumed
        assert any("Workflow code changed" in reason for reason in resumed["incompatible"]), resumed
        assert (folder / "effects").read_text().splitlines() == ["first"]
    else:
        assert resumed["status"] == "completed", resumed
        assert (folder / "effects").read_text().splitlines() == ["first", "saved-" + expected + " named"]
        assert len(requests) == 1
        assert "You name receipts." in json.dumps(requests[0])
