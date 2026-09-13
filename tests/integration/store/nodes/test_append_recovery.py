"""A new process recovers history committed immediately before producer exit."""
import subprocess
import sys

import pytest

from openprogram.store import SessionStore


PRODUCER = '''
import os
import sys
from pathlib import Path
from openprogram.context.nodes import Call
from openprogram.store import SessionStore, SessionNodeWriter
store = SessionStore(Path(sys.argv[1]))
store.create_session("crash", "main", title="retained")
store.append_message("crash", {"id": "root", "role": "user", "content": "root"})
git, _ = store._open("crash")
def interrupt(*args, **kwargs):
    os._exit(73)
git.write_meta = interrupt
if sys.argv[2] == "message":
    store.append_message("crash", {"id": "child", "role": "assistant", "predecessor": "root"})
else:
    SessionNodeWriter(store, "crash").append(Call(id="child", role="llm", predecessor="root"))
'''


@pytest.mark.parametrize("method", ["message", "writer"])
def test_abrupt_producer_exit_recovers_append(tmp_path, method):
    root = tmp_path / "sessions"
    result = subprocess.run(
        [sys.executable, "-c", PRODUCER, str(root), method],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 73, result.stderr
    store = SessionStore(root)
    try:
        session = store.get_session("crash")
        assert session["head_id"] == "child"
        assert session["title"] == "retained"
        assert [(n.id, n.seq) for n in store.get_nodes("crash")] == [("root", 0), ("child", 1)]
    finally:
        store.close()
