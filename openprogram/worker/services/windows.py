"""Windows Task Scheduler integration for the persistent worker.

The task is registered for the current user, runs with least privilege at
logon, starts immediately after installation, and retries after failures.
No administrator account or password is required.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

from openprogram.worker import paths as worker_paths

TASK_NAME = "OpenProgram Worker"
SCRIPT_NAME = "worker-service.cmd"


def _script_path() -> Path:
    return worker_paths.state_dir() / SCRIPT_NAME


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell(script: str) -> tuple[int, str]:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        return 127, "PowerShell not found"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def _batch_quote(value: str) -> str:
    """Quote one trusted absolute path for a generated cmd file."""

    return subprocess.list2cmdline([value]).replace("%", "%%")


def _build_script() -> str:
    from openprogram.worker.lifecycle import _detached_worker_command

    command = subprocess.list2cmdline(_detached_worker_command()).replace("%", "%%")
    home = _batch_quote(str(Path.home()))
    log = _batch_quote(str(worker_paths.log_path()))
    return "\r\n".join(
        [
            "@echo off",
            f"cd /d {home}",
            f"{command} >> {log} 2>&1",
            "exit /b %ERRORLEVEL%",
            "",
        ]
    )


def _register_script(task_script: Path) -> str:
    task = _ps_quote(TASK_NAME)
    action_args = _ps_quote(f'/d /s /c ""{task_script}""')
    working = _ps_quote(str(Path.home()))
    return (
        "$ErrorActionPreference='Stop';"
        f"$action=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument {action_args} "
        f"-WorkingDirectory {working};"
        "$trigger=New-ScheduledTaskTrigger -AtLogOn;"
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 "
        "-RestartInterval (New-TimeSpan -Minutes 1) "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew;"
        f"Register-ScheduledTask -TaskName {task} -Action $action -Trigger $trigger "
        "-Settings $settings -Description 'OpenProgram persistent worker' "
        "-Force | Out-Null;"
        f"Start-ScheduledTask -TaskName {task}"
    )


def install() -> int:
    from openprogram.worker.lifecycle import current_worker_pid, stop_worker

    if current_worker_pid() is not None and stop_worker() != 0:
        return 1

    task_script = _script_path()
    task_script.write_text(_build_script(), encoding="utf-8", newline="")
    from openprogram._compat import restrict_to_user

    restrict_to_user(task_script)
    rc, message = _powershell(_register_script(task_script))
    if rc != 0:
        print(f"Windows Task Scheduler install failed (rc={rc}): {message}")
        return rc or 1
    print(f"openprogram worker installed as a per-user scheduled task ({TASK_NAME}).")
    print(f"  launcher: {task_script}")
    print(f"  logs:     {worker_paths.log_path()}")
    print()
    print("It is now running and will start automatically at login.")
    print("Check status:  openprogram worker status")
    return 0


def uninstall() -> int:
    task = _ps_quote(TASK_NAME)
    script = (
        "$ErrorActionPreference='Stop';"
        f"$task=Get-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue;"
        "if($null -ne $task){"
        f"Stop-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue;"
        f"Unregister-ScheduledTask -TaskName {task} -Confirm:$false"
        "}"
    )
    rc, message = _powershell(script)
    if rc != 0:
        print(f"Windows Task Scheduler uninstall failed (rc={rc}): {message}")
        return rc or 1
    _script_path().unlink(missing_ok=True)
    print(f"openprogram worker uninstalled (removed scheduled task {TASK_NAME}).")
    return 0


def status() -> int:
    # Our installer writes the launcher before registering the task and removes
    # it only after unregistering succeeds. Most Desktop users never install
    # this optional task, so skip a several-second PowerShell cold start for
    # the overwhelmingly common negative case.
    if not _script_path().is_file():
        print(f"Windows scheduled task: {TASK_NAME}")
        print("  installed: no")
        return 0

    task = _ps_quote(TASK_NAME)
    script = (
        "$ErrorActionPreference='Stop';"
        f"$task=Get-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue;"
        "if($null -eq $task){exit 3};"
        f"$info=Get-ScheduledTaskInfo -TaskName {task};"
        "[Console]::Out.WriteLine('state=' + $task.State);"
        "[Console]::Out.WriteLine('last_run=' + $info.LastRunTime.ToString('o'));"
        "[Console]::Out.WriteLine('last_result=' + $info.LastTaskResult)"
    )
    rc, message = _powershell(script)
    print(f"Windows scheduled task: {TASK_NAME}")
    if rc == 3:
        print("  installed: no")
        return 0
    if rc != 0:
        print(f"  status query failed (rc={rc}): {message}")
        return rc or 1
    print("  installed: yes")
    for line in message.splitlines():
        print(f"  {line}")
    print(f"  launcher:  {_script_path()}")
    return 0


__all__ = ["TASK_NAME", "install", "status", "uninstall"]
