"""Existence checks must never signal the process being inspected."""
import os
import subprocess
import sys

from openprogram._compat import process_alive, process_start_token


def test_current_process_is_alive():
    assert process_alive(os.getpid())
    token = process_start_token(os.getpid())
    assert token and process_start_token(os.getpid()) == token
    assert not process_alive(0)
    assert not process_alive(-1)


def test_probe_keeps_child_alive_then_detects_exit():
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    ) as child:
        assert child.stdout.readline().rstrip(b"\r\n") == b"ready"
        for _ in range(5):
            assert process_alive(child.pid)
            assert child.poll() is None
        child.communicate(timeout=10)
        assert child.returncode == 0
        assert not process_alive(child.pid)
