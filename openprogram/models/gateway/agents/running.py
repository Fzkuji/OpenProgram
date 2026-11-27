"""Whether an agent's desktop app runs on what it read before the last change,
and restarting it.

After yetone/magpie ``internal/agent/apprestart.go``, ``running_other.go`` and
``runninglately.go`` (MIT License, Copyright (c) yetone). An app that reads
its config only as it starts keeps the old settings until it is restarted.
While it runs on what it read before OpenProgram's last write to its files,
its row offers a Restart (:func:`app_behind`). A Restart is the click asking
for it: the app is asked to quit as its own Quit does, every process of its
bundle waited for, and opened again. One still open after the wait (it may
be asking something in its window) is left, never forced.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Agent, App, app_bundles

#: How long a listing of running processes is reused (magpie ``runningFor``).
RUNNING_FOR = 3.0
QUIT_WAIT = 30.0
POLL = 0.5

_lately: dict[str, tuple[float, list[tuple[float, str]]]] = {}
_lately_lock = threading.Lock()
_restarting: dict[str, threading.Lock] = {}


def _elapsed(text: str) -> Optional[float]:
    """``ps``'s etime (``[[dd-]hh:]mm:ss``) in seconds."""
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        if not head.isdigit():
            return None
        days = int(head)
    parts = text.split(":")
    if not all(p.isdigit() for p in parts) or not 1 <= len(parts) <= 3:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return days * 86400 + seconds


def list_running(pattern: str) -> list[tuple[float, str]]:
    """``(seconds up, command line)`` of each process a ``pgrep -f`` pattern matches."""
    if sys.platform == "win32":
        return []
    try:
        pids = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10).stdout.split()
        if not pids:
            return []
        out = subprocess.run(["ps", "-o", "etime=,command=", "-p", ",".join(pids)],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.strip().splitlines():
        etime, _, command = line.strip().partition(" ")
        up = _elapsed(etime)
        if up is not None:
            found.append((up, command.strip()))
    with _lately_lock:
        _lately[pattern] = (time.monotonic(), [(time.time() - up, cmd) for up, cmd in found])
    return found


def running(pattern: str) -> list[tuple[float, str]]:
    """:func:`list_running` as it was up to :data:`RUNNING_FOR` ago."""
    with _lately_lock:
        kept = _lately.get(pattern)
    if kept and time.monotonic() - kept[0] < RUNNING_FOR:
        now = time.time()
        return [(now - started, cmd) for started, cmd in kept[1]]
    return list_running(pattern)


def any_running(*patterns: str) -> bool:
    return any(running(pattern) for pattern in patterns)


@dataclass
class AppRun:
    bundle: Path
    exe: Path
    since: float     # when its oldest process started


def _executable(bundle: Path) -> Optional[Path]:
    import plistlib
    try:
        with open(bundle / "Contents" / "Info.plist", "rb") as handle:
            name = plistlib.load(handle).get("CFBundleExecutable")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return bundle / "Contents" / "MacOS" / name if name else None


def find_app(app: App) -> Optional[AppRun]:
    """The app running now from one of its bundles (macOS)."""
    for bundle in app_bundles(app):
        exe = _executable(bundle)
        if exe is None:
            continue
        found = running("^" + re.escape(str(exe)) + "( |$)")
        if app.instance is not None:
            found = [(seconds, cmd) for seconds, cmd in found if app.instance(cmd)]
        if found:
            up = max(seconds for seconds, _ in found)
            return AppRun(bundle, exe, time.time() - up)
    return None


def app_behind(agent: Agent, changed_at: Optional[float]) -> Optional[AppRun]:
    """The agent's app when it runs on what it read before ``changed_at``."""
    if agent.app is None or not changed_at:
        return None
    run = find_app(agent.app)
    if run is None or not run.since < changed_at - 1:
        return None
    return run


def restart_app(agent: Agent, changed_at: Optional[float]) -> str:
    """Quit the agent's app as its own Quit does and open it again.

    ``""`` when it was restarted, else why not, in words for the row."""
    lock = _restarting.setdefault(agent.id, threading.Lock())
    with lock:
        run = app_behind(agent, changed_at)
        if run is None:
            return f"{agent.name} isn't running on the old settings any more."
        if agent.app.reopen is not None:
            said = agent.app.reopen(run)
            if said is not None:
                return said
        try:
            subprocess.run(["osascript", "-e", f'tell application id "{agent.app.bundle_id}" to quit'],
                           capture_output=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            return f"OpenProgram couldn't ask {agent.name} to quit. Quit it yourself, then open it again."
        pattern = re.escape(str(run.bundle) + "/Contents/")
        deadline = time.monotonic() + QUIT_WAIT
        while list_running(pattern):
            if time.monotonic() > deadline:
                return (f"{agent.name} is still open; it may be asking something in its window. "
                        "Answer it there, or quit it, then open it again.")
            time.sleep(POLL)
        try:
            subprocess.run(["open", str(run.bundle)], capture_output=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            return f"{agent.name} quit, but didn't open again. Open it yourself."
        return ""
