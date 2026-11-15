"""Windows Task Scheduler integration for the persistent worker.

The task is registered for the current user, runs with least privilege at
logon, starts immediately after installation, and retries after failures.
No administrator account or password is required. The default profile keeps
the historical task name; a named profile gets a task of its own, and its
launcher sets the profile, because the task does not inherit the environment
of the shell that installed it.

Once the task is installed it owns the worker: start and restart rewrite the
launcher for the runtime that runs the command and start the task again, and
stop ends the task's instance as well as the worker, so the retry policy
does not bring back a worker the user stopped.
"""
from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

from openprogram.host._platform.processes import console_text
from openprogram.operations.worker import paths as worker_paths

TASK_NAME = "OpenProgram Worker"
SCRIPT_NAME = "worker-service.cmd"


def _task_name() -> str:
    """Task name for the active profile, as the launchd label and systemd unit are."""
    from openprogram.state.paths import get_active_profile

    profile = get_active_profile()
    if profile is None:
        return TASK_NAME
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()[:16]
    return f"{TASK_NAME} profile-{digest}"


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
            # Bytes, not text: PowerShell answers in the console's code page.
            capture_output=True,
            timeout=30,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    output = console_text(result.stdout) + console_text(result.stderr)
    return result.returncode, output.strip()


def _batch_quote(value: str) -> str:
    """Quote one trusted absolute path for a generated cmd file."""

    return subprocess.list2cmdline([value]).replace("%", "%%")


# What the launcher sets before it starts the worker. Task Scheduler runs the
# task with the user's logon environment, not that of the shell that installed
# it, so without these a named profile's task would start the default
# profile's worker. PATH is left to the logon environment, which on Windows
# is the user's whole PATH. No secret belongs in this list.
_LAUNCHER_ENVIRONMENT = (
    "OPENPROGRAM_PROFILE",
    "OPENPROGRAM_STATE_DIR",
    "OPENPROGRAM_BIN_DIR",
    "OPENPROGRAM_WEB_PORT",
    "OPENPROGRAM_WORKDIR",
    "OPENPROGRAM_NO_WEB",
    "OPENPROGRAM_IMMUTABLE_RUNTIME",
    "PLAYWRIGHT_BROWSERS_PATH",
)


def _launcher_environment() -> dict[str, str]:
    """The installing command's values the worker needs at every logon."""
    from openprogram.operations.worker.services.systemd import _service_environment

    inherited = _service_environment()
    return {name: inherited[name] for name in _LAUNCHER_ENVIRONMENT if name in inherited}


def _batch_set(name: str, value: str) -> str:
    """One ``set "NAME=value"`` line of a generated cmd file.

    Inside the quotes cmd reads ``&``, ``|``, ``^`` and spaces as text, and
    ``%`` is doubled. A double quote or a line break has no spelling there,
    so such a value is refused instead of being written wrong.
    """
    if any(character in value for character in '"\r\n\0'):
        raise ValueError(f"{name} cannot be written to the worker launcher: {value!r}")
    return f'set "{name}={value}"'.replace("%", "%%")


def _build_script() -> str:
    from openprogram.operations.worker.lifecycle import _detached_worker_command

    command = subprocess.list2cmdline(_detached_worker_command()).replace("%", "%%")
    home = _batch_quote(str(Path.home()))
    log = _batch_quote(str(worker_paths.log_path()))
    lines = [
        "@echo off",
        *(_batch_set(name, value) for name, value in _launcher_environment().items()),
        f"cd /d {home}",
        f"{command} >> {log} 2>&1",
        "exit /b %ERRORLEVEL%",
        "",
    ]
    if not "\r\n".join(lines).isascii():
        # cmd reads a batch file in the console code page; the file is UTF-8.
        lines.insert(1, "chcp 65001 >nul")
    return "\r\n".join(lines)


def _register_script(task_script: Path) -> str:
    task = _ps_quote(_task_name())
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


def _stop_owned_worker() -> int:
    """Stop the worker, then end the task instance that ran it.

    The worker is asked first so it shuts down in order and its launcher
    exits zero; ``Stop-ScheduledTask`` alone ends the whole instance at once.
    The instance is ended afterwards all the same: a worker that had to be
    killed leaves a failed run, and a failed run is retried.
    """
    from openprogram.operations.worker.lifecycle import current_worker_pid, stop_worker

    if current_worker_pid() is not None and stop_worker(prefer_service=False) != 0:
        return 1
    if not _script_path().is_file():
        return 0
    task = _ps_quote(_task_name())
    rc, message = _powershell(
        "$ErrorActionPreference='Stop';"
        f"$task=Get-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue;"
        f"if($null -ne $task){{Stop-ScheduledTask -TaskName {task}}}"
    )
    if rc != 0:
        print(f"Windows Task Scheduler stop failed (rc={rc}): {message}")
        return rc or 1
    return 0


def start_if_installed() -> int | None:
    """Start the task-owned worker, or return ``None`` when no task is installed.

    Reinstalling rewrites the launcher for the current runtime first, so a
    start after an upgrade, and the next logon, never run an old interpreter.
    """
    if not _script_path().is_file():
        return None
    return install()


def stop_if_installed() -> int | None:
    """Stop the task-owned worker and keep it stopped until the next start or logon."""
    if not _script_path().is_file():
        return None
    rc = _stop_owned_worker()
    if rc == 0:
        print(f"openprogram worker stopped via Task Scheduler ({_task_name()}).")
    return rc


def restart_if_installed() -> int | None:
    """Refresh and restart an installed task using the current runtime."""
    if not _script_path().is_file():
        return None
    return install()


def install() -> int:
    try:
        launcher = _build_script()
    except ValueError as exc:
        print(f"Windows Task Scheduler install failed: {exc}")
        return 1
    rc = _stop_owned_worker()
    if rc != 0:
        return rc

    task_script = _script_path()
    task_script.write_text(launcher, encoding="utf-8", newline="")
    from openprogram.host._platform.filesystem import restrict_to_user

    restrict_to_user(task_script)
    rc, message = _powershell(_register_script(task_script))
    if rc != 0:
        print(f"Windows Task Scheduler install failed (rc={rc}): {message}")
        return rc or 1
    print(f"openprogram worker installed as a per-user scheduled task ({_task_name()}).")
    print(f"  launcher: {task_script}")
    print(f"  logs:     {worker_paths.log_path()}")
    print()
    print("It is now running and will start automatically at login.")
    print("Check status:  openprogram worker status")
    return 0


def uninstall() -> int:
    from openprogram.operations.worker.lifecycle import current_worker_pid, stop_worker

    if (_script_path().is_file() and current_worker_pid() is not None
            and stop_worker(prefer_service=False) != 0):
        return 1
    task = _ps_quote(_task_name())
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
    print(f"openprogram worker uninstalled (removed scheduled task {_task_name()}).")
    return 0


def status() -> int:
    # Our installer writes the launcher before registering the task and removes
    # it only after unregistering succeeds. Most Desktop users never install
    # this optional task, so skip a several-second PowerShell cold start for
    # the overwhelmingly common negative case.
    if not _script_path().is_file():
        print(f"Windows scheduled task: {_task_name()}")
        print("  installed: no")
        return 0

    task = _ps_quote(_task_name())
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
    print(f"Windows scheduled task: {_task_name()}")
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


__all__ = [
    "TASK_NAME",
    "install",
    "restart_if_installed",
    "start_if_installed",
    "status",
    "stop_if_installed",
    "uninstall",
]
