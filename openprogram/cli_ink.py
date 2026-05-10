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


def _print_no_worker_hint() -> None:
    """Tell the user how to start a worker."""
    print("openprogram: no worker is running.", file=sys.stderr)
    print(file=sys.stderr)
    print("The TUI connects to a persistent worker process that hosts the", file=sys.stderr)
    print("model API, sessions, and any chat-channel adapters. Start one:", file=sys.stderr)
    print(file=sys.stderr)
    print("  openprogram worker start          # one-off background process", file=sys.stderr)
    print("  openprogram worker install        # install as a system service", file=sys.stderr)
    print(file=sys.stderr)
    print("Then re-run `openprogram` to open the TUI.", file=sys.stderr)


def _resolve_worker_port(*, autostart: bool) -> int | None:
    """Find a live worker's port, optionally starting one if missing.

    With ``autostart=False`` (the strict mode the TUI uses), simply
    returns the port from ``worker.port`` if a worker is alive, else
    None. With ``autostart=True``, this could spawn a worker — kept as
    an opt-in for non-interactive flows that want the legacy convenience.
    """
    from openprogram.worker import current_worker_pid, read_worker_port, spawn_detached

    if current_worker_pid() is not None:
        port = read_worker_port()
        if port is not None and _wait_until_listening(port, timeout=2.0):
            return port

    if not autostart:
        return None

    rc = spawn_detached()
    if rc != 0:
        return None
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if current_worker_pid() is not None:
            port = read_worker_port()
            if port is not None and _wait_until_listening(port, timeout=0.5):
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
    node = _resolve_node()
    entry = _resolve_cli_entry()

    # Surface any update that was applied since the last launch. This
    # runs before the dup2 redirect so the user actually sees it on
    # their terminal instead of in ink-server.log.
    try:
        from openprogram.updater import pop_staged_notice
        notice = pop_staged_notice()
        if notice:
            target = notice.get("version") or "?"
            summary = notice.get("summary") or ""
            line = f"openprogram: updated to {target}"
            if summary and summary != "up to date":
                line += f" ({summary})"
            print(line, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass

    # Auto-start the worker if missing (overridable via env var for the rare
    # case where the user wants a strictly-connecting TUI). The worker manages
    # its own singleton lock, so concurrent CLI launches won't race-spawn.
    from openprogram.worker import current_worker_pid
    no_autostart = os.environ.get("OPENPROGRAM_NO_AUTO_WORKER", "").strip() in ("1", "true", "yes")
    autostart = not no_autostart
    started_here = autostart and current_worker_pid() is None
    if started_here:
        print("openprogram: starting worker…", file=sys.stderr)
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
