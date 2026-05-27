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
    """Return path to the built Ink TUI bundle, building it if needed.

    Fast path: ``cli/dist/index.js`` already exists → return immediately
    (one stat call). Cold path (first run on a fresh clone, any platform):
    transparently run ``npm install`` + ``npm run build`` in ``cli/`` and
    return the new bundle. Progress is streamed to the user's saved tty
    so they see it even after the TUI startup stdio redirect.

    The TUI source ships as TypeScript / TSX (React-for-terminal via
    Ink); Node can't execute it directly. The build is a one-time
    compile per machine — git ignores ``cli/dist/`` so every clone
    needs it. Before this autobuild, users had to read the README and
    run the commands manually; "I just ran openprogram and nothing
    happened" was the most common first-run report.

    Raises ``FileNotFoundError`` if ``cli/`` is missing (e.g. wheel
    install without the source tree) or if the build failed in a way
    that didn't produce the expected output.
    """
    here = Path(__file__).resolve()
    project_root = here.parent.parent
    cli_dir = project_root / "cli"
    candidate = cli_dir / "dist" / "index.js"
    if candidate.exists():
        return candidate

    if not cli_dir.exists():
        raise FileNotFoundError(
            f"Ink TUI source missing: no {cli_dir} directory. "
            "The TUI ships with the source tree — install openprogram "
            "from a git clone (``pip install -e .``) for the full "
            "experience, or use ``openprogram --web`` / ``--no-tui``."
        )

    _build_ink_bundle(cli_dir, candidate)

    if not candidate.exists():
        raise FileNotFoundError(
            f"Build completed but {candidate} still missing. "
            "Inspect the npm output above and re-run."
        )
    return candidate


def _build_ink_bundle(cli_dir: Path, expected_bundle: Path) -> None:
    """Run ``npm install`` (if needed) + ``npm run build`` in ``cli_dir``.

    Cross-platform. Streams npm's own output to the user's saved tty
    so they can see exactly what's happening (download progress,
    esbuild lines, errors). Skipping ``npm install`` when
    ``node_modules/`` already exists halves the cold-start cost on a
    re-build after a pull.
    """
    from openprogram import cli as _cli

    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError(
            "npm not found in PATH. Install Node.js 20+ (https://nodejs.org/) "
            "to build the TUI. Alternatively use ``openprogram --no-tui`` or "
            "``--web``."
        )

    # Stream to the saved-original tty if the TUI dup2 already happened,
    # otherwise stdout/stderr is fine (POSIX without TUI redirect, or
    # any non-TTY invocation).
    tty_out = getattr(_cli, "_TUI_TTY_OUT", None)
    tty_err = getattr(_cli, "_TUI_TTY_ERR", None)
    stdout_target = tty_out if tty_out is not None else None
    stderr_target = tty_err if tty_err is not None else None

    node_modules = cli_dir / "node_modules"
    if not node_modules.exists():
        _tty_write(
            "openprogram: building Ink TUI (first run, ~1-2 minutes)…\n"
            "  → npm install\n"
        )
        rc = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=str(cli_dir),
            stdout=stdout_target,
            stderr=stderr_target,
        ).returncode
        if rc != 0:
            raise RuntimeError(
                f"npm install failed (exit {rc}). Fix the error above and "
                "retry — the next ``openprogram`` will resume from where "
                "this left off."
            )
    else:
        _tty_write("openprogram: rebuilding Ink TUI (cli/dist/ missing)…\n")

    _tty_write("  → npm run build\n")
    rc = subprocess.run(
        [npm, "run", "build"],
        cwd=str(cli_dir),
        stdout=stdout_target,
        stderr=stderr_target,
    ).returncode
    if rc != 0:
        raise RuntimeError(
            f"npm run build failed (exit {rc}). Fix the error above and "
            "retry."
        )

    if expected_bundle.exists():
        _tty_write("  → built.\n\n")


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
    except (FileNotFoundError, RuntimeError) as e:
        # ``_resolve_cli_entry`` auto-builds the Ink bundle when
        # missing, so reaching here means either ``cli/`` is gone (wheel
        # install without source) or the npm install / build failed.
        # Either way, the inner error string already explains it; just
        # add the bail-out options.
        _tty_write(
            f"openprogram: {e}\n\n"
            "Alternatives that don't need the TUI:\n\n"
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
    # Track launch time so we can distinguish "TUI started, user
    # quit" (normal) from "TUI couldn't start at all" (raw-mode
    # failure, missing console, etc.) — the latter exits within
    # ~1 second with a non-zero code and we want to fall through
    # to the Rich REPL instead of leaving the user staring at a
    # silently-restored prompt.
    _t_launch = time.monotonic()
    # stdin handling: ``stdin=None`` (default) lets the OS inherit
    # the parent's stdin handle naturally. On Windows this is the
    # crucial path for Ink — passing ``stdin=0`` makes Python's
    # subprocess explicitly hand over a CRT-fd-derived handle with
    # ``STARTF_USESTDHANDLES``, and Node + Ink see that as a plain
    # handle rather than the console, so ``process.stdin.isTTY`` is
    # false and ``setRawMode`` throws. ``stdin=None`` keeps the
    # console attribute intact across the spawn boundary on both
    # Windows and POSIX (POSIX inherits fd 0 either way).
    proc = subprocess.Popen(cmd, env=env, stdout=tty_out, stderr=tty_err)
    user_interrupted = False
    try:
        proc.wait()
    except KeyboardInterrupt:
        user_interrupted = True
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()

    elapsed = time.monotonic() - _t_launch
    rc = proc.returncode or 0

    # Heuristic: Node + Ink that successfully entered raw-mode runs
    # until the user explicitly quits — minimum lifespan is seconds,
    # usually much more. A non-zero exit within 1.5 s almost always
    # means the TUI couldn't initialise (the canonical case on
    # Windows is "Raw mode is not supported on the current process
    # .stdin" from Ink, when subprocess inheritance didn't preserve
    # the console handle properly).
    tui_quick_fail = not user_interrupted and rc != 0 and elapsed < 1.5

    if tui_quick_fail:
        # Surface what just happened, while we still have working
        # handles. ``_tty_write`` goes directly to the saved tty fd via
        # ``os.write`` so it bypasses any Python-level stdio buffering
        # left over from the dup2 redirect — Rich's ``console.print``
        # in cli_chat's fallback path can't be trusted here because
        # ``sys.stdout`` was attached to a non-tty target (the log
        # file) and may have switched to block-buffered mode.
        _tty_write(
            "\n"
            f"openprogram: Ink TUI exited rc={rc} after {elapsed:.2f}s.\n"
            "  This usually means the terminal can't enter raw input mode\n"
            "  (Windows Git Bash / MinTTY, or a PowerShell + Node combo where\n"
            "  the console handle didn't pass through Python's subprocess).\n"
            "  Falling back to the Rich REPL (text-only chat).\n"
            "  Skip this attempt next time with: openprogram --no-tui\n"
            "\n"
        )
        # Restore stdio so the Rich REPL fallback in cli_chat writes to
        # the user's terminal instead of into the dup2-redirected log.
        # cli_chat does the same restore, but doing it here too is
        # safe (idempotent) and means even bare callers get a working
        # fallback. Don't close tty_out / tty_err afterward — they're
        # the SOURCE of the dup2, closing would kill the restored
        # stdio.
        for std_fd, saved in ((1, tty_out), (2, tty_err)):
            try:
                os.dup2(saved, std_fd)
            except OSError:
                pass
        raise RuntimeError(
            f"Ink TUI exited immediately (rc={rc}, after {elapsed:.2f}s). "
            "Falling back to the Rich REPL."
        )

    # Normal exit path — Node finished cleanly (user quit, or a real
    # runtime error after the TUI had been running long enough that
    # the user almost certainly saw it). Close our saved fds so we
    # don't leak, then exit.
    try:
        os.close(tty_out)
        os.close(tty_err)
    except OSError:
        pass
    sys.exit(rc)
