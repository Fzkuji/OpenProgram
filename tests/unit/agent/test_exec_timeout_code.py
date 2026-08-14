"""exec_command — a killed process must not report success.

``process.returncode`` is None when we never reaped an exit status
(timeout/cancel killed it, or communicate() blew up). Mapping that to 0
made a timed-out command look like a clean run to every caller.

Plain sync tests driving asyncio.run — the repo has no pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import sys


def test_timeout_reports_nonzero_and_notes_duration(tmp_path):
    from openprogram.agent.exec import ExecOptions, exec_command
    res = asyncio.run(exec_command(
        sys.executable, ["-c", "import time; time.sleep(30)"],
        str(tmp_path), ExecOptions(timeout=200),
    ))
    assert res.killed is True
    assert res.code != 0
    assert "timed out after 0.2s" in res.stderr


def test_cancel_reports_nonzero(tmp_path):
    from openprogram.agent.exec import ExecOptions, exec_command

    async def _run():
        sig = asyncio.Event()

        async def _cancel_soon():
            await asyncio.sleep(0.2)
            sig.set()

        task = asyncio.create_task(_cancel_soon())
        res = await exec_command(
            sys.executable, ["-c", "import time; time.sleep(30)"],
            str(tmp_path), ExecOptions(signal=sig),
        )
        await task
        return res

    res = asyncio.run(_run())
    assert res.killed is True
    assert res.code != 0
    assert "cancelled" in res.stderr


def test_clean_run_still_reports_zero(tmp_path):
    from openprogram.agent.exec import exec_command
    res = asyncio.run(exec_command(
        sys.executable, ["-c", "print('ok')"], str(tmp_path),
    ))
    assert res.code == 0
    assert res.killed is False
    assert res.stdout.strip() == "ok"
    assert res.stderr == ""


def test_failing_command_keeps_its_own_exit_code(tmp_path):
    from openprogram.agent.exec import exec_command
    res = asyncio.run(exec_command(
        sys.executable, ["-c", "import sys; sys.exit(3)"], str(tmp_path),
    ))
    assert res.code == 3
    assert res.killed is False
