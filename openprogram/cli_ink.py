"""Launch the Ink-based TUI front-end.

The CLI front-end is a Node.js program (cli/dist/index.js) that talks to the
Python webui server over WebSocket. ``run_ink_tui`` starts the server in
this process, picks a free port, and spawns the Node child with stdin/stdout
attached so it owns the terminal.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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


def run_ink_tui(*, agent=None, conv_id: str | None = None, rt=None) -> None:
    """Start the webui server, then exec the Node CLI as a child.

    The agent / conv_id / rt arguments are kept for signature compatibility
    with the old Textual entry; the Node front-end discovers the default
    agent over the ws ``list_agents`` action and picks its own conv_id when
    the user sends the first message.
    """
    from openprogram.webui import start_web

    node = _resolve_node()
    entry = _resolve_cli_entry()

    port = _find_free_port()

    # cli.py already did the early dup2 for the TUI path and stashed the
    # original tty fds on the cli module. Reuse those so the Node child
    # gets a clean terminal while the server's threads keep writing into
    # ~/.openprogram/logs/ink-startup.log. Fall back to a fresh redirect
    # if the early hook didn't run (e.g. when run_ink_tui is called from
    # somewhere other than the cli entry point).
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

    # If a channels worker is already running it has its OWN webui in
    # the same process; attach to that instead of starting a competing
    # one. Sharing the webui process is what makes inbound wechat msgs
    # show up live in the TUI (channel threads' `_broadcast` only
    # reaches clients connected to the asyncio loop in their own
    # process). Otherwise start our own webui as before.
    from openprogram.channels.worker import read_worker_port
    worker_port = read_worker_port()
    if worker_port is not None:
        port = worker_port
    else:
        # Don't wait for the ws server to listen before spawning Node — Node
        # takes ~340ms to load its bundle (React + Ink + marked + ws), in
        # parallel with the server's ~240ms boot. BackendClient retries with
        # exponential backoff so the first connect attempt may fail and that's
        # fine.
        start_web(port=port, open_browser=False)

    ws_url = f"ws://127.0.0.1:{port}/ws"
    env = os.environ.copy()
    env["OPENPROGRAM_WS"] = ws_url
    if agent is not None and getattr(agent, "id", None):
        env["OPENPROGRAM_AGENT"] = agent.id
    if conv_id:
        env["OPENPROGRAM_CONV"] = conv_id

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
