"""A command run in a terminal ends with its group and keeps all its output."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

GUARD = Path(__file__).resolve().parents[3] / "openprogram" / "host" / "processes" / "group_guard.py"


def _run_in_terminal(command):
    guard = subprocess.Popen([sys.executable, str(GUARD)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    launch = {"args": command, "shell": True, "_tty": True}
    started = time.monotonic()
    output, errors = guard.communicate((json.dumps(launch) + "\n").encode(), timeout=30)
    return guard.returncode, output, errors, time.monotonic() - started


@pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal")
def test_terminal_output_ends_when_the_group_exits():
    code, output, errors, elapsed = _run_in_terminal("echo done")
    assert code == 0, errors
    assert output == b"done\r\n"
    # The pump stops with the group instead of waiting out its join bound.
    assert elapsed < 1.5, elapsed


@pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal")
def test_terminal_keeps_every_byte_written_right_before_exit():
    size = 400_000
    writer = shlex.join([sys.executable, "-c",
                         f"import os; data = b'x' * {size}; n = 0\n"
                         "while n < len(data): n += os.write(1, data[n:])"])
    code, output, errors, _ = _run_in_terminal(f"stty raw -echo; {writer}")
    assert code == 0, errors
    assert output == b"x" * size


@pytest.mark.skipif(os.name != "posix", reason="POSIX pseudo-terminal")
def test_terminal_keeps_output_of_a_job_in_another_group():
    # Under job control the background job gets a group of its own but stays
    # in the session and on the terminal; what it writes after the command's
    # group ends still arrives, and the guard ends as soon as the job does.
    code, output, errors, elapsed = _run_in_terminal(
        "set -m; (sleep 0.5; echo late) & echo early")
    assert code == 0, errors
    assert output == b"early\r\nlate\r\n"
    assert elapsed < 1.5, elapsed
