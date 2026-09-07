"""Real persistent-child acceptance; no host UI capability is exposed."""
import sys
import threading
import time

import pytest

from openprogram.backend.gui_runner import GuiPythonRunner, GuiRunnerError

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")


def test_persistent_namespace_await_and_error_recovery():
    with GuiPythonRunner() as runner:
        assert runner.execute("value = 40").error is None
        result = runner.execute("import asyncio\nawait asyncio.sleep(0)\nvalue += 2\nprint(value)")
        assert result.stdout == "42\n"
        assert runner.execute("raise ValueError('owned failure')").error == "ValueError: owned failure"
        assert runner.execute("print(value)").stdout == "42\n"


def test_timeout_closes_child_without_replay():
    with GuiPythonRunner() as runner:
        start = time.monotonic()
        with pytest.raises(GuiRunnerError, match="deadline"):
            runner.execute("while True: pass", timeout=0.2)
        assert time.monotonic() - start < 3
        with pytest.raises(GuiRunnerError, match="closed"):
            runner.execute("print('must not restart')")


def test_cancellation_closes_running_child():
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    with GuiPythonRunner() as runner:
        timer.start()
        try:
            with pytest.raises(GuiRunnerError, match="cancelled"):
                runner.execute("while True: pass", cancel=cancel)
        finally:
            timer.cancel()
            timer.join()


@pytest.mark.parametrize("code, message", [
    ("import os; os.write(1, b'xxxx')", "frame"),
    ("import os; os.write(1, b'\\x00\\x00\\x00\\x01!')", "protocol"),
    ("import os; os.write(2, b'x' * 400000)", "output"),
    ("print('x' * 400000)", "output"),
    ("import os; os._exit(0)", "exited"),
])
def test_untrusted_output_fails_closed(code, message):
    with GuiPythonRunner() as runner:
        with pytest.raises(GuiRunnerError, match=message):
            runner.execute(code, timeout=3)
        with pytest.raises(GuiRunnerError, match="closed"):
            runner.execute("pass")


def test_raw_stderr_before_completion_is_not_lost():
    with GuiPythonRunner() as runner:
        assert runner.execute("import os; os.write(2, b'owned stderr')").stderr == "owned stderr"


def test_forged_completion_cannot_make_next_large_input_block_host():
    code = """
import os, json, struct
payload = json.dumps({'id': 1, 'type': 'done', 'error': None}).encode()
os.write(1, struct.pack('!I', len(payload)) + payload)
while True: pass
"""
    with GuiPythonRunner() as runner:
        runner.execute(code)
        start = time.monotonic()
        with pytest.raises(GuiRunnerError, match="deadline"):
            runner.execute("#" + "x" * 200000, timeout=0.2)
        assert time.monotonic() - start < 3


def test_idle_child_is_suspended_until_next_call():
    with GuiPythonRunner() as runner:
        runner.execute("import threading, time\nvalues = []\ndef work():\n while True:\n  values.append(1)\n  time.sleep(0.001)\nthreading.Thread(target=work, daemon=True).start()")
        # The host owns this child; kernel stop status proves background code
        # cannot consume CPU or produce output while no execute call is active.
        import subprocess
        status = subprocess.check_output(["/bin/ps", "-o", "state=", "-p", str(runner._process.pid)], text=True)
        assert "T" in status
        assert runner.execute("print('resumed')").stdout == "resumed\n"


def test_concurrent_execute_does_not_cancel_active_call():
    cancel = threading.Event()
    failures = []
    with GuiPythonRunner() as runner:
        def execute():
            try:
                runner.execute("while True: pass", cancel=cancel)
            except GuiRunnerError as exc:
                failures.append(str(exc))
        thread = threading.Thread(target=execute)
        thread.start()
        try:
            deadline = time.monotonic() + 2
            while not runner._lock.locked() and time.monotonic() < deadline:
                time.sleep(0.001)
            assert runner._lock.locked()
            with pytest.raises(GuiRunnerError, match="concurrent"):
                runner.execute("pass")
        finally:
            cancel.set()
            thread.join(timeout=3)
        assert not thread.is_alive()
        assert failures == ["execution cancelled"]


def test_raw_stderr_unicode_split_across_pipe_reads():
    with GuiPythonRunner() as runner:
        result = runner.execute("import os; os.write(2, b'a' * 65535 + '中文'.encode())")
        assert result.stderr == "a" * 65535 + "中文"


def test_empty_text_frames_still_have_a_transport_budget():
    code = """
import os, json, struct
payload = json.dumps({'id': 1, 'type': 'output', 'stream': 'stdout', 'text': '', 'padding': 'x' * 1000000}).encode()
frame = struct.pack('!I', len(payload)) + payload
for _ in range(10): os.write(1, frame)
"""
    with GuiPythonRunner() as runner:
        with pytest.raises(GuiRunnerError, match="transport limit"):
            runner.execute(code, timeout=3)


def test_context_error_reaps_suspended_process_and_removes_scratch():
    import os
    from pathlib import Path
    with pytest.raises(RuntimeError, match="caller error"):
        with GuiPythonRunner() as runner:
            result = runner.execute("import os; print(os.getpid()); print(os.getcwd())")
            pid_text, directory = result.stdout.splitlines()
            scratch = Path(directory)
            assert scratch.exists()
            raise RuntimeError("caller error")
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_text), 0)
    assert not scratch.exists()
