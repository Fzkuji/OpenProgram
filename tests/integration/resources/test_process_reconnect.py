"""The durable supervisor survives its worker and retains process identity/output."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.support.waiting import wait_until


PROGRAM = r'''
import asyncio, json, shlex, sys, threading
from pathlib import Path
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
from openprogram.runtime.context.session_db import default_db
from openprogram.runtime.permissions.authority import local_owner_authority
from openprogram.host.processes import start

folder = Path(sys.argv[1])
default_db().create_session('process-test', 'main', work_dir=str(folder))
def work(*, request, cancel_event):
    command = shlex.join([sys.executable, '-u', '-c',
        "import sys; print('ready', flush=True); print(sys.stdin.readline().strip(), flush=True)"])
    record = start(command, cwd=str(folder), tty=True)
    (folder / 'process.json').write_text(json.dumps(record))
    threading.Event().wait(25)
    raise TimeoutError('test did not terminate worker')
adapter = CanonicalAgentAdapter(turn_runner=work)
admission = adapter.admit(TurnRequest('process-test', 'Run test process', 'main', 'web'),
    trusted_actor=local_owner_authority(), user_message_id='user', config_snapshot_ref='test')
asyncio.run(adapter.activate(admission))
'''


@pytest.mark.skipif(os.name != "posix", reason="POSIX worker kill and detached supervisor acceptance")
def test_new_worker_reconnects_same_managed_process_after_owner_is_killed(tmp_path):
    from openprogram.host.processes import ProcessStore, control
    home = tmp_path / "home"
    home.mkdir()
    program = tmp_path / "worker.py"
    program.write_text(PROGRAM)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "OPENPROGRAM_PROFILE": "",
           "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    store = ProcessStore(home / ".openprogram" / "processes.db")
    worker = subprocess.Popen([sys.executable, str(program), str(tmp_path)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_until(lambda: (tmp_path / "process.json").exists() or worker.poll() is not None, timeout=60)
        assert worker.poll() is None, worker.communicate(timeout=2)
        initial = json.loads((tmp_path / "process.json").read_text())
        assert initial["status"] == "running", initial
        worker.kill()
        worker.communicate(timeout=30)
        # A fresh interpreter uses the durable identity and existing supervisor;
        # it does not call start() or signal any PID loaded from the database.
        result = subprocess.run([sys.executable, "-c", r'''
import json, sys
from openprogram.host.processes import ProcessStore, control
from tests.support.waiting import wait_until
store = ProcessStore(sys.argv[1])
record = store.get(sys.argv[2])
assert record['status'] == 'running', record
# Type only once the program is reading; earlier, the echo lands before 'ready'.
assert wait_until(lambda: 'ready' in store.output(record['id']), timeout=30)
control(store, record['id'], 'write', 'continued-once\n')
assert wait_until(lambda: store.get(record['id'])['status'] == 'exited', timeout=30)
print(json.dumps({'record': store.get(record['id']), 'output': store.output(record['id'])}))
''', str(store.path), initial["id"]], env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        observed = json.loads(result.stdout.strip().splitlines()[-1])
        for key in ("id", "pid", "pid_identity", "supervisor_pid", "supervisor_identity"):
            assert observed["record"][key] == initial[key]
        # The terminal echoes the typed line before the program prints it back.
        assert [line.strip() for line in observed["output"].splitlines()] == [
            "ready", "continued-once", "continued-once"]
        assert observed["record"]["exit_code"] == 0
        assert len(store.list()) == 1
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)
        for record in store.list():
            if record["status"] in {"starting", "running", "stopping"}:
                control(store, record["id"], "stop")
