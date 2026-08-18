"""Config-driven shell subscribers (Claude Code hooks exit-code protocol).

The top-level ``hooks`` key in config.json (``config_schema`` setting
``hooks``) maps event type → list of ``{"command": str, "timeout": s?}``.
:func:`install_config_hooks` reads it once at worker start and registers
each command on the bus: gate-kind events get a synchronous shell gate,
notify-kind events get a background runner. Config edits take effect on
the next worker start.

Protocol (the Event arrives as JSON on the command's stdin):

* gate — exit 0 allows; exit 2 denies with stderr as the reason; any other
  exit code, and a timeout, is fail-open with a warning.
* notify — runs on a daemon thread, exit code ignored, failures logged.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading

from openprogram.events.registry import EVENTS

_log = logging.getLogger(__name__)

DEFAULT_SHELL_TIMEOUT_S = 60


def _event_json(ev) -> str:
    return json.dumps(
        {"id": ev.id, "ts": ev.ts, "type": ev.type, "origin": ev.origin,
         "payload": ev.payload, "metadata": ev.metadata},
        ensure_ascii=False, default=str,
    )


def make_shell_gate(command: str, timeout_s: float = DEFAULT_SHELL_TIMEOUT_S):
    """A gate function backed by a shell command."""
    def gate(ev):
        try:
            proc = subprocess.run(
                command, shell=True, input=_event_json(ev),
                capture_output=True, text=True, timeout=timeout_s,
            )
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
                        timeout_s: float = DEFAULT_SHELL_TIMEOUT_S):
    """A notify subscriber backed by a shell command — never blocks the
    emitter."""
    def notify(ev):
        payload = _event_json(ev)

        def _run() -> None:
            try:
                proc = subprocess.run(
                    command, shell=True, input=payload,
                    capture_output=True, text=True, timeout=timeout_s,
                )
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
        from openprogram import setup as _setup
        hooks_config = _setup._read_config().get("hooks") or {}
    if bus is None:
        from openprogram.events.bus import get_event_bus
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
            if spec.kind == "gate":
                bus.subscribe_gate(event_type, make_shell_gate(command, timeout))
            else:
                bus.subscribe(make_shell_notifier(command, timeout),
                              types={event_type})
            count += 1
    return count
