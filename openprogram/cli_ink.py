"""Launch the Ink-based TUI front-end.

The TUI is a Node.js program (cli/dist/index.js) that talks to the
OpenProgram worker over WebSocket. The worker must already be running
— ``run_ink_tui`` looks up the live worker via ``worker.{pid,port}``
and connects. If no worker is running we print actionable hints
(``openprogram worker start`` / ``openprogram worker install``) and
exit. The TUI no longer spawns a temporary backend of its own; the
backend is a single, long-lived process shared by all front-ends.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_listening(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


def _resolve_cli_entry() -> Path:
    here = Path(__file__).resolve()
    project_root = here.parent.parent
    candidate = project_root / "cli" / "dist" / "index.js"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Ink CLI bundle not found at {candidate}. "
        f"Run `cd cli && npm install && npm run build` first."
    )


def _resolve_node() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError(
            "node binary not found in PATH. Install Node.js (>=20) to use the TUI."
        )
    return node


def _tty_write(msg: str) -> None:
    """Write ``msg`` to the user's actual terminal even when
    :mod:`openprogram.cli` has already dup2'd ``sys.stderr`` to the
    Ink startup log.

    Without this, the "no worker is running" hint and similar
    actionable errors would land in
    ``~/.openprogram/logs/ink-startup.log`` and the user would see a
    silent prompt return — exactly the "I ran openprogram and nothing
    happened" bug.
    """
    from openprogram import cli as _cli
    fd = getattr(_cli, "_TUI_TTY_ERR", None)
    if fd is None:
        # Redirect didn't happen (non-tty / non-TUI launch / earlier
        # error). Plain stderr is fine.
        try:
            sys.stderr.write(msg)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        return
    data = msg.encode("utf-8", errors="replace")
    try:
        os.write(fd, data)
    except OSError:
        # Saved fd somehow invalid (e.g. terminal closed underneath
        # us) — last-ditch attempt at sys.stderr, may also fail.
        try:
            sys.stderr.write(msg)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


def _print_no_worker_hint() -> None:
    """Tell the user how to start a worker. Always writes to the
    saved original tty, so the message survives the TUI startup
    stdio redirect.
    """
    _tty_write(
        "openprogram: no worker is running.\n"
        "\n"
        "The TUI connects to a persistent worker process that hosts the\n"
        "model API, sessions, and any chat-channel adapters. Start one:\n"
        "\n"
        "  openprogram worker start          # one-off background process\n"
        "  openprogram worker install        # install as a system service\n"
        "\n"
        "Then re-run `openprogram` to open the TUI.\n"
    )


def _resolve_worker_port(*, autostart: bool) -> int | None:
    """Find a live webui port, optionally starting a worker if none.

    Three sources, in order:

    1. A managed worker (``worker.lock`` + ``worker.port``). The
       well-supported path; ``worker stop`` / ``restart`` know about it.
    2. An unmanaged webui — i.e. a foreground ``openprogram --web``
       process that the user launched themselves. Doesn't write the
       lock files, but ``find_running_webui()`` discovers it via a
       TCP probe on the default port. The TUI can talk to it just
       fine (same WS protocol).
    3. ``autostart=True`` and nothing is up — spawn a detached
       worker and wait briefly for it to start.

    Returns the port number on success, ``None`` on failure (caller
    prints the "no worker" hint).
    """
    from openprogram.worker import spawn_detached
    from openprogram.worker.lifecycle import find_running_webui

    port, _pid, source = find_running_webui()
    if source != "none":
        # Already up — managed or unmanaged, doesn't matter for the TUI.
        if port is not None and _wait_until_listening(port, timeout=2.0):
            return port

    if not autostart:
        return None

    rc = spawn_detached()
    if rc != 0:
        # Common cause on Windows: port already in use by a foreground
        # ``--web`` instance whose lock file we couldn't detect for
        # some reason. Surface a more actionable error.
        _tty_write(
            "openprogram: couldn't start a worker (likely port in use).\n"
            "If you have ``openprogram --web`` running in another terminal,\n"
            "that webui is what the TUI should connect to — but its port\n"
            "wasn't detected. Stop it and either rerun `openprogram` (TUI\n"
            "will auto-start a managed worker) or run\n"
            "``openprogram worker start`` first.\n"
        )
        return None
    deadline = time.time() + 8.0
    while time.time() < deadline:
        port, _pid, source = find_running_webui()
        if source != "none" and port is not None:
            if _wait_until_listening(port, timeout=0.5):
                return port
        time.sleep(0.1)
    return None


def run_ink_tui(*, agent=None, session_id: str | None = None, rt=None) -> None:
    """Connect the Node TUI to the live worker.

    The agent / session_id / rt arguments are kept for signature compatibility
    with the old Textual entry; the Node front-end discovers the default
    agent over the ws ``list_agents`` action and picks its own session_id when
    the user sends the first message.
    """
    # Resolve the Node binary + the built Ink bundle. Both errors are
    # actionable for the user but trivial to surface invisibly — at
    # this point ``cli._maybe_redirect_for_tui`` has already pointed
    # stderr at ``~/.openprogram/logs/ink-startup.log``, so an
    # uncaught FileNotFoundError would land there and the user would
    # see ``openprogram`` exit with no output. Route them through
    # ``_tty_write`` instead.
    try:
        node = _resolve_node()
    except RuntimeError as e:
        _tty_write(f"openprogram: {e}\n")
        sys.exit(2)
    try:
        entry = _resolve_cli_entry()
    except FileNotFoundError as e:
        _tty_write(
            f"openprogram: {e}\n\n"
            "The terminal UI ships as a Node.js bundle that needs to be\n"
            "built once. From the repo root:\n\n"
            "  cd cli\n"
            "  npm install\n"
            "  npm run build\n\n"
            "Or skip the TUI entirely:\n\n"
            "  openprogram --no-tui          # Rich-based REPL (text only)\n"
            "  openprogram --web             # browser UI\n"
            "  openprogram --print \"hi\"      # one-shot prompt\n"
        )
        sys.exit(2)

    # Surface any update that was applied since the last launch.
    # Goes to the saved tty so the user sees it even after the dup2
    # redirect that ``cli._maybe_redirect_for_tui`` performed at
    # module import.
    try:
        from openprogram.updater import pop_staged_notice
        notice = pop_staged_notice()
        if notice:
            target = notice.get("version") or "?"
            summary = notice.get("summary") or ""
            line = f"openprogram: updated to {target}"
            if summary and summary != "up to date":
                line += f" ({summary})"
            _tty_write(line + "\n")
    except Exception:  # noqa: BLE001
        pass

    # Auto-start the worker if missing (overridable via env var for the rare
    # case where the user wants a strictly-connecting TUI). The worker manages
    # its own singleton lock, so concurrent CLI launches won't race-spawn.
    from openprogram.worker.lifecycle import find_running_webui
    no_autostart = os.environ.get("OPENPROGRAM_NO_AUTO_WORKER", "").strip() in ("1", "true", "yes")
    autostart = not no_autostart
    _port, _pid, _source = find_running_webui()
    started_here = autostart and _source == "none"
    if started_here:
        _tty_write("openprogram: starting worker…\n")
    port = _resolve_worker_port(autostart=autostart)
    if port is None:
        _print_no_worker_hint()
        sys.exit(2)

    # cli.py already did the early dup2 for the TUI path and stashed the
    # original tty fds on the cli module. Reuse those so the Node child
    # gets a clean terminal while logs land in ~/.openprogram/logs/.
    from openprogram import cli as _cli
    tty_out = getattr(_cli, "_TUI_TTY_OUT", None)
    tty_err = getattr(_cli, "_TUI_TTY_ERR", None)
    if tty_out is None or tty_err is None:
        log_dir = Path.home() / ".openprogram" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "ink-server.log"
        tty_out = os.dup(1)
        tty_err = os.dup(2)
        log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)

    ws_url = f"ws://127.0.0.1:{port}/ws"
    env = os.environ.copy()
    env["OPENPROGRAM_WS"] = ws_url
    if agent is not None and getattr(agent, "id", None):
        env["OPENPROGRAM_AGENT"] = agent.id
    if session_id:
        env["OPENPROGRAM_CONV"] = session_id

    cmd = [node, str(entry), "--ws", ws_url]
    proc = subprocess.Popen(cmd, env=env, stdin=0, stdout=tty_out, stderr=tty_err)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        try:
            os.close(tty_out)
            os.close(tty_err)
        except OSError:
            pass
        sys.exit(proc.returncode or 0)
