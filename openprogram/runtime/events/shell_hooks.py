"""Config-driven shell subscribers (Claude Code hooks exit-code protocol).

The top-level ``hooks`` key in config.json (``state.settings`` setting
``hooks``) maps event type → list of ``{"command": str, "timeout": s?}``.
:func:`install_config_hooks` reads it once at worker start and registers
each command on the bus: gate-kind events get a synchronous shell gate,
notify-kind events get a background runner. Config edits take effect on
the next worker start.

Protocol (the Event arrives as JSON on the command's stdin):

* gate — exit 0 allows; exit 2 denies with stderr as the reason; any other
  exit code, and a timeout, is fail-open with a warning.
* notify — runs on a daemon thread, exit code ignored, failures logged.

An entry may say which language its command is written in, as Claude Code
hooks do: ``"shell": "bash"`` (the default) or ``"shell": "powershell"``.
With ``"args": [...]`` the entry runs ``[command, *args]`` directly, through
no shell at all, so no quoting rule applies.

The command runs in ``/bin/sh`` on macOS and Linux (``pwsh`` for a
PowerShell hook). On Windows a bash hook runs in the host's bash (Git Bash,
found as ``host._platform.command_shell`` finds it) and a PowerShell hook in
PowerShell, never ``cmd.exe``, so exit code 2 arrives as 2. A bash hook on a
Windows host without bash runs in PowerShell instead and the conversation is
told once (``hook_shell_fallback``) how to fix it. A hook that cannot run
still allows the action. The event and the hook's output are UTF-8 on every
platform.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading

from openprogram.runtime.events.registry import EVENTS

_log = logging.getLogger(__name__)

DEFAULT_SHELL_TIMEOUT_S = 60


def _event_json(ev) -> str:
    return json.dumps(
        {"id": ev.id, "ts": ev.ts, "type": ev.type, "origin": ev.origin,
         "payload": ev.payload, "metadata": ev.metadata},
        ensure_ascii=False, default=str,
    )


SHELLS = ("bash", "powershell")
FALLBACK_NOTICE = (
    "A hook written for bash ran in PowerShell, because this computer has no bash. "
    "Install Git for Windows, give the hook \"shell\": \"powershell\", or write it as "
    "\"command\" plus \"args\" so it runs without a shell."
)
_told: set[str] = set()
_told_lock = threading.Lock()


def _session_of(ev) -> str:
    metadata = getattr(ev, "metadata", None) or {}
    payload = getattr(ev, "payload", None) or {}
    return str(metadata.get("session") or (payload.get("session_id") if isinstance(payload, dict) else "") or "")


def _tell_fallback_once(ev) -> None:
    """Say once per conversation that a bash hook ran in PowerShell."""
    session = _session_of(ev)
    with _told_lock:
        if session in _told:
            return
        _told.add(session)
    _log.warning("hook_shell_fallback (session %s): %s", session or "-", FALLBACK_NOTICE)
    from openprogram.runtime.events.bus import emit_ws_frame
    emit_ws_frame({"type": "hook_shell_fallback",
                   "data": {"session_id": session or None, "message": FALLBACK_NOTICE}})


def _run_hook(command: str, payload: str, timeout_s: float, *, shell: str = "bash",
              args: list[str] | None = None, ev=None) -> subprocess.CompletedProcess:
    argv: str | list[str] = command
    use_shell, env = True, None
    flags = 0
    if sys.platform == "win32":
        from openprogram.host._platform.process_ownership import no_window_creation_flags
        flags = no_window_creation_flags()
    if args is not None:
        argv, use_shell = [command, *args], False
    elif sys.platform == "win32":
        from openprogram.host._platform import command_shell
        from openprogram.host.backend.local import host_shell_invocation
        host = command_shell.host_shell()
        if shell == "powershell":
            profile = host if host.is_powershell else command_shell.windows_powershell()
        else:
            profile = host
            if host.is_powershell:
                _tell_fallback_once(ev)
        argv, use_shell, env = host_shell_invocation(command, profile=profile)
    elif shell == "powershell":
        import shutil
        from openprogram.host._platform.terminal import powershell_invocation
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            raise FileNotFoundError("a PowerShell hook needs pwsh on this computer")
        argv, env = powershell_invocation(pwsh, command)
        use_shell = False
    return subprocess.run(
        argv, shell=use_shell, env=env, input=payload,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout_s, creationflags=flags,
    )


def make_shell_gate(command: str, timeout_s: float = DEFAULT_SHELL_TIMEOUT_S, *,
                    shell: str = "bash", args: list[str] | None = None):
    """A gate function backed by a shell command."""
    def gate(ev):
        try:
            proc = _run_hook(command, _event_json(ev), timeout_s, shell=shell, args=args, ev=ev)
        except subprocess.TimeoutExpired:
            _log.warning("shell gate timed out after %ss (fail-open): %s",
                         timeout_s, command)
            return None
        except Exception as exc:  # noqa: BLE001 — a broken hook must not brick the action
            _log.warning("shell gate failed to run (fail-open): %s: %s",
                         command, exc)
            return None
        if proc.returncode == 0:
            return None
        if proc.returncode == 2:
            return (proc.stderr or "").strip() or f"denied by hook: {command}"
        _log.warning("shell gate exited %s (fail-open): %s",
                     proc.returncode, command)
        return None

    return gate


def make_shell_notifier(command: str,
                        timeout_s: float = DEFAULT_SHELL_TIMEOUT_S, *,
                        shell: str = "bash", args: list[str] | None = None):
    """A notify subscriber backed by a shell command — never blocks the
    emitter."""
    def notify(ev):
        payload = _event_json(ev)

        def _run() -> None:
            try:
                proc = _run_hook(command, payload, timeout_s, shell=shell, args=args, ev=ev)
                if proc.returncode != 0:
                    _log.info("shell notifier exited %s: %s",
                              proc.returncode, command)
            except Exception as exc:  # noqa: BLE001
                _log.warning("shell notifier failed: %s: %s", command, exc)

        threading.Thread(target=_run, daemon=True).start()

    return notify


def install_config_hooks(bus=None, hooks_config: dict | None = None) -> int:
    """Register the user's config.json ``hooks`` on the bus.

    Unknown event types are skipped with a warning. Returns the number of
    subscribers registered.
    """
    if hooks_config is None:
        from openprogram.state import config as user_config
        hooks_config = user_config.read_config().get("hooks") or {}
    if bus is None:
        from openprogram.runtime.events.bus import get_event_bus
        bus = get_event_bus()

    count = 0
    for event_type, entries in (hooks_config or {}).items():
        spec = EVENTS.get(event_type)
        if spec is None:
            _log.warning("hooks config: unknown event type %r skipped "
                         "(registered types: %s)",
                         event_type, ", ".join(sorted(EVENTS)))
            continue
        for entry in entries or []:
            command = (entry or {}).get("command")
            if not isinstance(command, str) or not command.strip():
                _log.warning("hooks config: %s entry without a command "
                             "skipped", event_type)
                continue
            try:
                timeout = float(entry.get("timeout") or DEFAULT_SHELL_TIMEOUT_S)
            except (TypeError, ValueError):
                timeout = DEFAULT_SHELL_TIMEOUT_S
            shell = entry.get("shell") or "bash"
            if shell not in SHELLS:
                _log.warning("hooks config: %s entry names shell %r; it runs in bash",
                             event_type, shell)
                shell = "bash"
            args = entry.get("args")
            if args is not None and not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
                _log.warning("hooks config: %s entry has args that are not a list of strings; "
                             "entry skipped", event_type)
                continue
            if spec.kind == "transform":
                _log.warning("hooks config: %s rewrites a value; shell hooks "
                             "cannot, entry skipped", event_type)
                continue
            if spec.kind == "gate":
                bus.subscribe_gate(event_type, make_shell_gate(command, timeout, shell=shell, args=args))
            else:
                bus.subscribe(make_shell_notifier(command, timeout, shell=shell, args=args),
                              types={event_type})
            count += 1
    return count
