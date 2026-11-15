"""A rewind that restores files holds them through a durable record another
process can see: operations there are refused until the holder lets go, and
a holder that died is marked cancelled and holds nothing."""
from __future__ import annotations

from openprogram.state.store.session import operations
def test_a_rewind_held_by_another_process_refuses_operations_here(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path

    from openprogram.interfaces.server import server

    root = tmp_path / "projects"
    script = (
        "import sys\n"
        "from openprogram.state.store.session import operations\n"
        f"operations.record_operation('far-s1', {{'op_id': 'op_far', 'kind': 'rewind', "
        f"'branch_id': None, 'scope': 'conversation'}}, root={str(root)!r})\n"
        "print('held', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    repo = Path(__file__).resolve().parents[3]
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=repo, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert proc.stdout.readline().strip() == "held"
        monkeypatch.setattr(operations, "_root", lambda root_=None: root)
        refusal = server.reserve_operation("far-s1", "compact", "compact_far")
        assert (refusal["code"], refusal["kind"], refusal["op_id"]) == ("run_active", "rewind", "op_far")
        assert not server._try_reserve_run("far-s1", "reply_far")
    finally:
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(timeout=30)
    # The holder exited without finishing: its record no longer holds.
    assert operations.workspace_hold("far-s1") is None
    assert server.reserve_operation("far-s1", "compact", "compact_far") is None
    server._release_run_reservation("far-s1", "compact_far")
    assert operations.sweep_interrupted(root, force=True) == 1
    assert [op["op_id"] for op in operations.interrupted_operations("far-s1")] == ["op_far"]
