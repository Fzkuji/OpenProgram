"""Real local child cleanup and execution-scoped cancellation."""

import shlex
import sys
import time

from openprogram.backend.local import LocalBackend


def _python(code):
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def test_timeout_retains_output_and_stops_child(tmp_path):
    marker = tmp_path / "delayed"
    result = LocalBackend().run(
        _python(
            f"import time; from pathlib import Path; print('partial', flush=True); "
            f"time.sleep(1); Path({str(marker)!r}).touch()"
        ),
        timeout=0.2,
    )
    assert result.timed_out
    assert "partial" in result.stdout
    time.sleep(1.1)
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
        command = _python(f"from pathlib import Path; Path({str(marker)!r}).touch()")
        result = LocalBackend().run(command, timeout=2)
        assert result.exit_code != 0
        assert "cancelled" in result.stderr
        assert not marker.exists()
        end_turn(sid, token)
        token = begin_turn(sid)
        assert LocalBackend().run(command, timeout=2).exit_code == 0
        assert marker.exists()
    finally:
        end_turn(sid, token)
        reset_current_session_id(binding)
