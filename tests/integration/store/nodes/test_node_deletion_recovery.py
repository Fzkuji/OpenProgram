"""A real process exit during deletion is recovered before a public read."""
from contextlib import closing
import subprocess
import sys

import pytest

from openprogram.context.nodes import Call
from openprogram.store import SessionNodeWriter, SessionStore


@pytest.mark.parametrize("method", ["drop_message", "delete_branch_tail"])
def test_process_exit_after_history_unlink_recovers_metadata(tmp_path, method):
    root = tmp_path / "sessions"
    with closing(SessionStore(root)) as store:
        store.create_session("s", "main")
        writer = SessionNodeWriter(store, "s")
        writer.append(Call(id="root", role="user"))
        writer.append(Call(id="retained", role="llm", predecessor="root"))
        writer.append(Call(id="target", role="llm", predecessor="root"))
        if method == "delete_branch_tail":
            writer.append(Call(id="child", role="tool", caller="target"))
    code = r"""
import os
import sys
from pathlib import Path
from openprogram.store import SessionStore
store = SessionStore(Path(sys.argv[1]))
original = Path.unlink
def crash_after_unlink(path, *args, **kwargs):
    result = original(path, *args, **kwargs)
    if path.parent.name == 'history' and path.name.endswith('-target.json'):
        os._exit(73)
    return result
Path.unlink = crash_after_unlink
getattr(store, sys.argv[2])('s', 'target')
"""
    result = subprocess.run([sys.executable, "-c", code, str(root), method], timeout=30)
    assert result.returncode == 73
    with closing(SessionStore(root)) as fresh:
        assert {n.id for n in fresh.get_nodes("s")} == {"root", "retained"}
        assert fresh.get_session("s")["head_id"] == "root"
        assert not (fresh._session_dir("s") / ".git" / "openprogram-delete-nodes.json").exists()
