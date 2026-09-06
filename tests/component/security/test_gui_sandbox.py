"""The GUI launch boundary must isolate real generated Python, even without outer sandboxing."""
import json
import sys
from pathlib import Path

import pytest

from openprogram import sandbox
from openprogram.backend.gui import spawn_gui_python


def run_script(code, outer):
    token = sandbox._execution_policy_override.set(outer)
    try:
        with spawn_gui_python(code) as process:
            stdout, stderr = process.communicate(timeout=10)
    finally:
        sandbox._execution_policy_override.reset(token)
    assert process.returncode == 0, stderr
    return json.loads(stdout)


@pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS backend acceptance")
@pytest.mark.parametrize("outer", [None, sandbox.SandboxPolicy(
    writable_roots=("/",), deny_read=(), deny_write=(), network=True, pass_env=("GUI_TEST_SECRET",),
    host_process_info=True)])
def test_gui_floor_survives_disabled_and_permissive_outer_policy(tmp_path, monkeypatch, outer):
    outside = tmp_path / "outside.txt"
    outside.write_text("owned outside fixture")
    monkeypatch.setenv("GUI_TEST_SECRET", "must-not-be-inherited")
    code = f'''
import json, os, socket
from pathlib import Path
results = {{}}
def attempt(name, fn):
    try:
        fn()
    except OSError:
        results[name] = "denied"
    else:
        results[name] = "allowed"
Path("owned.txt").write_text("scratch")
results["scratch"] = Path("owned.txt").read_text()
attempt("read", lambda: Path({str(outside)!r}).read_text())
attempt("write", lambda: Path({str(outside)!r}).write_text("changed"))
Path("link").symlink_to({str(outside)!r})
attempt("symlink", lambda: Path("link").read_text())
def fork():
    pid = os.fork()
    if pid == 0: os._exit(0)
    os.waitpid(pid, 0)
attempt("fork", fork)
attempt("network", lambda: socket.socket().bind(("127.0.0.1", 0)))
results["secret"] = os.environ.get("GUI_TEST_SECRET")
print(json.dumps(results))
'''
    result = run_script(code, outer)
    assert result == {"scratch": "scratch", "read": "denied", "write": "denied",
                      "symlink": "denied", "fork": "denied", "network": "denied", "secret": None}
    assert outside.read_text() == "owned outside fixture"


@pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS backend acceptance")
def test_gui_floor_keeps_outer_denial_inside_scratch(tmp_path):
    result = run_script('''
import json
from pathlib import Path
Path("denied.txt").write_text("owned")
try:
    Path("denied.txt").read_text()
except PermissionError:
    print(json.dumps("denied"))
else:
    print(json.dumps("allowed"))
''', sandbox.SandboxPolicy(deny_read=("**/denied.txt",), deny_write=()))
    assert result == "denied"


def test_gui_launch_never_falls_back_without_a_supported_sandbox(tmp_path, monkeypatch):
    from openprogram.backend import gui
    monkeypatch.setattr(gui.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "missing sandbox")
    with pytest.raises(sandbox.SandboxUnavailable, match="missing sandbox"):
        with spawn_gui_python("pass"):
            pytest.fail("unsandboxed launch")


def test_gui_launch_rejects_unsupported_platform(monkeypatch):
    from openprogram.backend import gui
    monkeypatch.setattr(gui.sys, "platform", "linux")
    with pytest.raises(sandbox.SandboxUnavailable, match="requires macOS"):
        with spawn_gui_python("pass"):
            pytest.fail("unsupported launch")


@pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS backend acceptance")
def test_gui_context_reaps_child_and_removes_scratch_after_caller_error():
    with pytest.raises(RuntimeError, match="caller failed"):
        with spawn_gui_python("import os, time; print(os.getcwd(), flush=True); time.sleep(60)") as process:
            scratch = Path(process.stdout.readline().decode().strip())
            assert scratch.is_dir()
            assert process.poll() is None
            raise RuntimeError("caller failed")
    assert process.poll() is not None
    assert not scratch.exists()
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
