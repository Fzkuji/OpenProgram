"""Real local child cleanup and execution-scoped cancellation."""

import shlex
import sys
import time
from pathlib import Path

from openprogram.backend.local import LocalBackend


def _python(code, tmp_path):
    from openprogram.backend import local

    # Exercise cancellation of real code, not nested `python -c` source
    # escaping through MSYS/native command-line parsing on Windows.
    script = tmp_path / "child.py"
    script.write_text(code, encoding="utf-8")
    arguments = [Path(sys.executable).as_posix(), script.as_posix()]
    if sys.platform == "win32" and local._windows_bash() is None:
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in arguments)
    return shlex.join(arguments)


def test_timeout_retains_output_and_stops_child(tmp_path):
    marker = tmp_path / "delayed"
    result = LocalBackend().run(
        _python(
            f"import time; from pathlib import Path; print('partial', flush=True); "
            f"time.sleep(3); Path({str(marker)!r}).touch()",
            tmp_path,
        ),
        # Budget includes native shell/interpreter cold startup, not just the
        # sleep in the child. A 200ms limit can expire before any output exists.
        timeout=2,
        cwd=str(tmp_path),
    )
    assert result.timed_out
    assert "partial" in result.stdout
    time.sleep(3.1)
    assert not marker.exists()


def test_pre_cancelled_execution_never_starts_shell_or_cancels_next_turn(tmp_path):
    from openprogram.agent.run_control import (
        begin_turn,
        end_turn,
        set_current_session_id,
        reset_current_session_id,
    )

    marker = tmp_path / "started"
    sid = "backend-cancellation-test"
    binding = set_current_session_id(sid)
    token = begin_turn(sid)
    try:
        token.cancel()
        command = _python(f"from pathlib import Path; Path({str(marker)!r}).touch()", tmp_path)
        result = LocalBackend().run(command, timeout=2, cwd=str(tmp_path))
        assert result.exit_code != 0
        assert "cancelled" in result.stderr
        assert not marker.exists()
        end_turn(sid, token)
        token = begin_turn(sid)
        assert LocalBackend().run(command, timeout=2, cwd=str(tmp_path)).exit_code == 0
        assert marker.exists()
    finally:
        end_turn(sid, token)
        reset_current_session_id(binding)


def test_uncertain_cancellation_cleanup_does_not_return_a_completion(monkeypatch):
    import subprocess
    import pytest
    from openprogram.agentic_programming.function import CancelledError
    from openprogram.backend import local
    from openprogram.agent.run_control import (
        begin_turn, end_turn, set_current_session_id, reset_current_session_id,
    )

    for terminated, drained in [(False, True), (True, False)]:
        sid = 'backend-uncertain-cancellation'
        binding = set_current_session_id(sid)
        token = begin_turn(sid)
        class Process:
            returncode = -9
            calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                token.cancel()
                if self.calls == 1 or not drained:
                    raise subprocess.TimeoutExpired('child', timeout, b'partial')
                return 'partial', ''

            def kill(self):
                pass

            def wait(self, timeout=None):
                return -9

        class Owner:
            def popen(self, *_a, **_kw):
                return Process()

            def terminate(self):
                return terminated

            def release(self):
                raise AssertionError('cancelled ownership must not be released')

        monkeypatch.setattr(local, 'ProcessTreeOwner', Owner)
        monkeypatch.setattr(local, '_invocation', lambda *_a, **_kw: ('unused', True, None, False))
        try:
            with pytest.raises(CancelledError):
                LocalBackend().run('unused', timeout=2)
        finally:
            end_turn(sid, token)
            reset_current_session_id(binding)
