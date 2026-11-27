"""Claude Science: its daemon restarted with ``ANTHROPIC_BASE_URL`` on the gateway.

After yetone/magpie ``internal/agent/claudescience.go`` (MIT License,
Copyright (c) yetone); the gateway side is ``gateway/science.py``. magpie's
WSL hosts (``claudescience_wsl.go``) are not ported.

Claude Science is Anthropic's research app ("operon" inside): a daemon,
``claude-science``, that serves its UI on localhost, and a window that shows
it (a Swift launcher on the Mac, Electron on Windows, a browser on Linux) and
attaches to the daemon running, else starts one. The daemon asks Anthropic's
Messages API at its environment's ``ANTHROPIC_BASE_URL`` (https, or http on a
loopback address) and has no setting for it, so OpenProgram connects it by
starting the daemon itself with that variable set to the gateway — ``stop``,
then ``serve --detached --no-browser``, on the port it was on — and nothing
else: no variable set for the user, no file of its written. Disconnecting
restarts it without the variable. The app still needs its own Claude sign-in,
which it sends as its bearer and the gateway never passes on: the address it
is given carries its own key (``Agent.path_keyed``), so its bearer is neither
checked nor used, and each turn goes upstream with OpenProgram's provider key.

The daemon lives till it is stopped or the computer restarts, so while it is
connected OpenProgram starts it again when it isn't running (:func:`keep_once`,
the Worker's keeper). The app attaches its window to a daemon running; with
none it starts its own (``serve --stdio --adopt-install``) in its own
environment, which has no such variable, and its "let the app run it instead"
stops OpenProgram's to do the same. So the keeper also puts OpenProgram's
daemon back in place of one it didn't start, and of its own once the app
updated past it, but only while no session runs in it, and never one a
service manager keeps (it would come back at once); the row says so meanwhile.

OpenProgram's record of the connection is ``claude-science.json`` in its
gateway folder (the address the daemon is given); the daemon it started
(pid, port) is noted beside it in ``claude-science.daemon.json``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .base import Agent, Field, Wiring, Write

_log = logging.getLogger(__name__)

ID = "claude-science"
NAME = "Claude Science"
#: The variable its daemon reads the API's address from.
VAR = "ANTHROPIC_BASE_URL"
#: How long a daemon started may take to say it runs.
WAIT = 30.0
#: How many times in an hour the keeper puts its daemon in place of another
#: before it leaves the other running: one the user keeps putting back is theirs.
RETAKES = 3

_mu = threading.Lock()  # one start or stop of the daemon at a time


def find_science(home: Optional[Path] = None) -> str:
    """Where ``claude-science`` is installed: in the Mac app's bundle,
    Windows' per-user Programs folder, or the ``~/.local/bin`` the installers
    link it into (the only place on Linux), else on PATH."""
    home = home or Path.home()
    at: list[Path] = []
    if sys.platform == "darwin":
        for folder in (Path("/Applications"), home / "Applications"):
            at.append(folder / f"{NAME}.app" / "Contents" / "Resources" / "bin" / "claude-science")
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            at.append(Path(local) / "Programs" / "ClaudeScience" / "claude-science.exe")
        at.append(home / "AppData" / "Local" / "Programs" / "ClaudeScience" / "claude-science.exe")
    exe = "claude-science.exe" if sys.platform == "win32" else "claude-science"
    at.append(home / ".local" / "bin" / exe)
    for path in at:
        if path.is_file():
            return str(path)
    return shutil.which("claude-science") or ""


_seen: dict[str, Any] = {"bin": "", "at": 0.0}


def science_bin() -> str:
    """The daemon's program, ``""`` when it isn't installed (looked for at
    most every 30 seconds). Tests replace it."""
    if time.monotonic() - _seen["at"] > 30 or not _seen["at"]:
        _seen["bin"], _seen["at"] = find_science(), time.monotonic()
    return _seen["bin"]


def science_dir() -> Path:
    """The daemon's data folder, ``~/.claude-science``. Tests replace it."""
    return Path.home() / ".claude-science"


#: Of OpenProgram's environment, what the daemon is not given: an Anthropic
#: key or token of OpenProgram's process would go out beside its own sign-in,
#: and OpenProgram's own variables are none of its business.
HELD_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def science_env(gw: str) -> dict[str, str]:
    """OpenProgram's environment with the variable set to ``gw``, or without it
    for ``""``, less :data:`HELD_ENV` and every ``OPENPROGRAM_…`` variable."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() != VAR and k.upper() not in HELD_ENV and not k.upper().startswith("OPENPROGRAM_")}
    if gw:
        env[VAR] = gw
    return env


def science_run(env: Optional[dict[str, str]], bin_: str, *args: str) -> bytes:
    """Run the daemon's program ``bin_`` with ``args`` in ``env`` (``None``:
    OpenProgram's own) and return what it printed. Tests replace it. ``serve
    --detached`` returns once its daemon is up; the daemon keeps none of the
    pipes, which go to no file at all."""
    if args and args[0] == "serve":
        # a detached daemon holding a pipe would hold the output till it quits
        subprocess.run([bin_, *args], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=90, check=True)
        return b""
    return subprocess.run([bin_, *args], env=env, capture_output=True, timeout=90, check=True).stdout


def run(bin_: str, gw: str, *args: str) -> bytes:
    """A serve with the variable set to ``gw`` (``""``: without it); anything else as OpenProgram runs."""
    return science_run(science_env(gw) if args and args[0] == "serve" else None, bin_, *args)


@dataclass
class Status:
    """What ``claude-science status`` says of its daemon."""

    running: bool = False
    pid: int = 0
    port: int = 0
    version: str = ""
    daemon: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, data: Any) -> "Status":
        data = data if isinstance(data, dict) else {}
        daemon = data.get("daemon") if isinstance(data.get("daemon"), dict) else {}
        return cls(bool(data.get("running")), int(data.get("pid") or 0), int(data.get("port") or 0),
                   str(data.get("version") or ""), daemon)

    def idle(self) -> bool:
        """No session runs in the daemon, as its status says (unknown: busy)."""
        frames, conversations = self.daemon.get("active_frames"), self.daemon.get("active_conversations")
        return frames == 0 and conversations == 0 and frames is not None and conversations is not None

    def kept(self) -> bool:
        """A service manager (launchd, systemd) started the daemon and would start it again."""
        return self.daemon.get("launch_context") in ("launchd", "systemd")


class ScienceError(Exception):
    pass


def lock() -> Optional[Status]:
    """The daemon running as its lock file says (``operon.lock``), ``None`` when
    there is none. Read where status would take seconds (a 130 MB Bun build)."""
    try:
        data = json.loads((science_dir() / "operon.lock").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = Status.of(data)
    if not status.pid:
        return None
    status.running = True
    return status


def state(bin_: str) -> Status:
    try:
        out = run(bin_, "", "status")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScienceError(f"claude-science status: {exc}") from exc
    try:
        return Status.of(json.loads(out or b"null"))
    except ValueError as exc:
        raise ScienceError(f"claude-science status said {out.decode(errors='replace').strip()!r}: {exc}") from exc


# OpenProgram's notes

def mark_path() -> Path:
    """The connection's record: the address the daemon is given (the engine writes it)."""
    from ..keys import gateway_dir
    return gateway_dir() / "claude-science.json"


def daemon_path() -> Path:
    """The daemon OpenProgram started on the gateway: its address, pid and port."""
    from ..keys import gateway_dir
    return gateway_dir() / "claude-science.daemon.json"


def read_daemon() -> Optional[dict[str, Any]]:
    try:
        data = json.loads(daemon_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None if not daemon_path().exists() else {}
    return data if isinstance(data, dict) else {}


def write_daemon(url: str, status: Status) -> None:
    # the address carries the agent's key: only its owner may read it
    from ..edit import write_atomic
    write_atomic(daemon_path(), json.dumps({"url": url, "pid": status.pid, "port": status.port}), keep_mode=False)


def gateway_url() -> str:
    """The address the daemon is given now: the one the engine recorded, ``""`` when off."""
    try:
        data = json.loads(mark_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("url") or "") if isinstance(data, dict) else ""


# Starting and stopping

def restart(bin_: str, gw: str, start: bool = True, was: Optional[Status] = None) -> Status:
    """Stop the daemon when it runs and start it on ``gw`` (``""``: without
    the variable), on the port it was on; only stop it for not ``start``.
    ``was`` is its status just asked, else it is asked."""
    was = was or state(bin_)
    if was.running:
        try:
            run(bin_, "", "stop")
        except (OSError, subprocess.SubprocessError) as exc:
            raise ScienceError(f"claude-science stop: {exc}") from exc
    if not start:
        return Status()
    return serve(bin_, gw, was.port)


def serve(bin_: str, gw: str, port: int) -> Status:
    """Start the daemon on ``gw`` (``""``: without the variable), on ``port``
    when it isn't 0, and wait till status says it runs."""
    args = ["serve", "--detached", "--no-browser"]
    if port > 0:
        args += ["--port", str(port)]
    try:
        run(bin_, gw, *args)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScienceError(f"claude-science serve: {exc}") from exc
    deadline = time.monotonic() + WAIT
    while True:
        try:
            status = state(bin_)
            if status.running:
                return status
        except ScienceError:
            pass
        if time.monotonic() > deadline:
            raise ScienceError("claude-science serve: its daemon isn't running (see claude-science logs)")
        time.sleep(WAIT / 60)


def on(connected: bool, gw: str = "") -> None:
    """Connected: the daemon restarted on the gateway at ``gw``, and noted;
    off: restarted without the variable, when it ran, and the note dropped."""
    with _mu:
        bin_ = science_bin()
        if not connected:
            if read_daemon() is not None and bin_:
                if state(bin_).running:
                    restart(bin_, "", True)
            daemon_path().unlink(missing_ok=True)
            return
        if not bin_:
            raise ScienceError(f"{NAME} isn't installed (no claude-science found)")
        # the daemon OpenProgram started, on this gateway, is left running
        note = read_daemon()
        status = state(bin_)
        if note and note.get("url") == gw and status.running and status.pid == note.get("pid"):
            return
        # a session running in the daemon would be cut off: it is left as it
        # is, noted as not OpenProgram's, and the keeper puts OpenProgram's in
        # its place once no session runs in it, as it does for any other
        if status.running and not status.idle():
            write_daemon(gw, Status(port=status.port))
            return
        write_daemon(gw, restart(bin_, gw, True, was=status))


def _on_route(_folder: Path, connected: bool, _record: dict[str, Any]) -> None:
    from .engine import AgentConfigError
    try:
        on(connected, gateway_url() if connected else "")
    except ScienceError as exc:
        raise AgentConfigError(str(exc)) from exc


def check() -> str:
    """What of the wiring is gone, by the daemon's lock file: a daemon running
    that OpenProgram didn't start (the window, opened while none ran, starts
    its own), or one started for the gateway's old address. One not running
    is no drift: the keeper starts it."""
    note = read_daemon()
    gw = gateway_url()
    if note is None or not gw:
        return ""
    running = lock()
    if running is None:
        return ""
    if running.pid != note.get("pid"):
        return (f"{NAME}'s daemon (pid {running.pid}) was started without OpenProgram's gateway (its window "
                "starts its own when none runs); OpenProgram starts its own in its place once no session runs in it")
    if note.get("url") != gw:
        # the address carries the agent's key: it is never shown
        return f"{NAME}'s daemon was started for the gateway's earlier address"
    return ""


# The keeper

@dataclass
class _Keeper:
    asked: float = 0.0
    lock_pid: int = 0
    bin: str = ""
    bin_size: int = -1
    bin_time: float = 0.0
    version: str = ""
    retook: list[float] = field(default_factory=list)


_keep = _Keeper()


def version(bin_: str) -> str:
    """The version of the program at ``bin_``, ``""`` unknown, asked again only
    when the file changed (the app updated)."""
    try:
        info = os.stat(bin_)
    except OSError:
        return ""
    if _keep.bin == bin_ and _keep.bin_size == info.st_size and _keep.bin_time == info.st_mtime:
        return _keep.version
    try:
        out = run(bin_, "", "--version").decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    # "claude-science 0.1.72 (release, public)"
    words = out.split()
    _keep.bin, _keep.bin_size, _keep.bin_time = bin_, info.st_size, info.st_mtime
    _keep.version = words[1] if len(words) > 1 else ""
    return _keep.version


def older_version(a: str, b: str) -> bool:
    """Dotted version ``a`` is older than ``b``; false when either doesn't read as one."""
    left, right = a.split("."), b.split(".")
    for i in range(max(len(left), len(right))):
        try:
            x = int(left[i]) if i < len(left) else 0
            y = int(right[i]) if i < len(right) else 0
        except ValueError:
            return False
        if x != y:
            return x < y
    return False


def stale(bin_: str, status: Status, note: dict[str, Any], gw: str) -> str:
    """Why the daemon running isn't the one OpenProgram keeps, ``""`` when it is."""
    if status.pid != note.get("pid"):
        return f"pid {status.pid} isn't the one OpenProgram started"
    if note.get("url") != gw:
        return "it was started for the gateway's earlier address"
    installed = version(bin_)
    if installed and older_version(status.version, installed):
        return f"it runs {status.version} and {installed} is installed"
    return ""


def keep_once() -> None:
    """Start the daemon on the gateway, while connected, when it isn't
    running, and in place of one OpenProgram didn't start or that is older
    than the program installed, once no session runs in it (magpie
    ``KeepClaudeScience``). It reads the lock file and asks status only when
    the lock's pid changed or a minute went by."""
    gw = gateway_url()
    if not gw:
        return
    with _mu:
        bin_ = science_bin()
        if not bin_:
            return
        running = lock()
        lock_pid = running.pid if running else 0
        if lock_pid == _keep.lock_pid and time.monotonic() - _keep.asked < 60:
            return
        try:
            status = state(bin_)
        except ScienceError:
            return
        finally:
            _keep.asked, _keep.lock_pid = time.monotonic(), lock_pid
        note = read_daemon() or {}
        port = int(note.get("port") or 0)
        if status.running:
            why = stale(bin_, status, note, gw)
            # a session running in it would be cut off; a service would
            # start it again without the variable
            if not why or not status.idle() or status.kept():
                return
            now = time.monotonic()
            _keep.retook = [t for t in _keep.retook if now - t < 3600]
            if len(_keep.retook) >= RETAKES:
                return
            _keep.retook.append(now)
            _log.info("%s: starting its daemon on OpenProgram's gateway again: %s", NAME, why)
            try:
                run(bin_, "", "stop")
            except (OSError, subprocess.SubprocessError) as exc:
                _log.info("%s: claude-science stop: %s", NAME, exc)
                return
            port = status.port
        try:
            status = serve(bin_, gw, port)
        except ScienceError as exc:
            _log.info("%s: starting its daemon on OpenProgram's gateway: %s", NAME, exc)
            return
        _keep.lock_pid = status.pid
        write_daemon(gw, status)


def _wire(at: Wiring) -> list[Write]:
    if not science_bin():
        from .engine import AgentConfigError
        raise AgentConfigError(f"{NAME} isn't installed (no claude-science found)")
    return [Write(mark_path(), "json", sets={"url": at.base("anthropic")})]


def _notice() -> str:
    why = check()
    if why:
        return why
    return (f"{NAME}'s daemon runs on OpenProgram's gateway: open {NAME} again (reload its window if it is "
            "open). It still needs you signed in with your Claude account, which OpenProgram never sends on; "
            "pick a model in its model menu (it lists OpenProgram's).")


def agent() -> Agent:
    return Agent(
        id=ID, name=NAME, icon="anthropic", aliases=("operon", "science"), kind="app",
        protocol="anthropic", detect=lambda: bool(science_bin()), path=mark_path,
        # its picker lists the gateway's /v1/models as it is shown them
        # (gateway/science.py); the model picked here is the one it is served
        # until it picks one itself
        fields=[Field("model", "Model")],
        named_key=True, path_keyed=True, wire=_wire, on_route=_on_route, notice=_notice,
        keep=keep_once,
    )
