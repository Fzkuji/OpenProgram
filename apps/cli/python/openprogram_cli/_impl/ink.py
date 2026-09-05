"""Launch the Ink-based TUI front-end.

The TUI is a Node.js program (apps/cli/dist/index.js) that talks to the
OpenProgram worker over WebSocket. The worker must already be running
— ``run_ink_tui`` looks up the live worker via ``worker.{pid,port}``
and connects. If no worker is running we print actionable hints
(``openprogram worker start`` / ``openprogram worker install``) and
exit. The TUI no longer spawns a temporary backend of its own; the
backend is a single, long-lived process shared by all front-ends.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openprogram._compat import (
    node_tool_cmd,
    tui_child_requires_direct_stdio_inheritance,
    tui_worker_ready_timeout_seconds,
)


_TUI_READY_ENV = "_OPENPROGRAM_TUI_READY_FILE"
_TUI_READY_MARKER = "OpenProgram Ink TUI first frame ready\n"


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


def _managed_runtime_root() -> Path | None:
    """Locate an immutable product runtime from the running interpreter.

    Release layouts differ slightly by platform, so detect the runtime
    manifest instead of branching on ``sys.platform`` or assuming a fixed
    Python directory depth.
    """
    candidates = (Path(sys.executable).resolve(), Path(__file__).resolve())
    seen: set[Path] = set()
    for candidate in candidates:
        for parent in (candidate.parent, *candidate.parents):
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / "runtime-manifest.json").is_file():
                return parent
    return None


def _resolve_cli_entry() -> Path:
    """Return path to the built Ink TUI bundle, building it if needed.

    Fast path: ``apps/cli/dist/index.js`` already exists → return immediately
    (one stat call). Cold path (first run on a fresh clone, any platform):
    transparently verify root workspace dependencies and build ``apps/cli/`` and
    return the new bundle. Progress is streamed to the user's saved tty
    so they see it even after the TUI startup stdio redirect.

    The TUI source ships as TypeScript / TSX (React-for-terminal via
    Ink); Node can't execute it directly. The build is a one-time
    compile per machine — git ignores ``apps/cli/dist/`` so every clone
    needs it. Before this autobuild, users had to read the README and
    run the commands manually; "I just ran openprogram and nothing
    happened" was the most common first-run report.

    Wheel installs use the self-contained ``openprogram_cli/dist/index.mjs``
    bundle without npm or a source checkout. Missing release assets or a
    failed source build raise ``FileNotFoundError``.
    """
    runtime_root = _managed_runtime_root()
    if runtime_root is not None:
        bundled = runtime_root / "assets" / "tui" / "index.cjs"
        if bundled.is_file():
            return bundled
        raise FileNotFoundError(
            f"Packaged Ink TUI is missing: {bundled}. "
            "Reinstall the complete OpenProgram release."
        )

    import openprogram

    project_root = Path(openprogram.__file__).resolve().parents[1]
    cli_dir = project_root / "apps" / "cli"
    candidate = cli_dir / "dist" / "index.js"
    if candidate.exists():
        return candidate

    if not cli_dir.exists():
        bundled = Path(__file__).resolve().parents[1] / "dist" / "index.mjs"
        if bundled.is_file():
            return bundled
        raise FileNotFoundError(
            f"Ink TUI source missing: no {cli_dir} directory. "
            "Source checkouts prepare it with `uv sync`; packaged runtimes "
            "fall back to the built-in Rich terminal UI."
        )

    _build_ink_bundle(cli_dir, candidate)

    if not candidate.exists():
        raise FileNotFoundError(
            f"Build completed but {candidate} still missing. "
            "Inspect the npm output above and re-run."
        )
    return candidate


def _build_ink_bundle(cli_dir: Path, expected_bundle: Path) -> None:
    """Verify root workspace dependencies and build the CLI bundle.

    Cross-platform. Streams npm's own output to the user's saved tty
    so they can see exactly what's happening (download progress,
    esbuild lines and errors). npm performs an incremental install so a
    partial install of another workspace cannot leave the CLI without esbuild.
    """
    from openprogram import cli as _cli

    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError(
            "npm not found in PATH. Install Node.js 20+ (https://nodejs.org/) "
            "to build the TUI. Alternatively use ``openprogram web`` "
            "for the browser UI."
        )

    # Stream to the saved-original tty if the TUI dup2 already happened,
    # otherwise stdout/stderr is fine (POSIX without TUI redirect, or
    # any non-TTY invocation).
    tty_out = getattr(_cli, "_TUI_TTY_OUT", None)
    tty_err = getattr(_cli, "_TUI_TTY_ERR", None)
    stdout_target = tty_out if tty_out is not None else None
    stderr_target = tty_err if tty_err is not None else None

    repo_root = cli_dir.parents[1]
    _tty_write(
        "openprogram: building Ink TUI (apps/cli/dist/ missing)…\n"
        "  → verifying npm workspace deps\n"
    )
    rc = subprocess.run(
        node_tool_cmd([npm, "install", "--no-audit", "--no-fund", "--loglevel=error"]),
        cwd=str(repo_root),
        stdout=stdout_target,
        stderr=stderr_target,
    ).returncode
    if rc != 0:
        raise RuntimeError(
            f"npm install failed (exit {rc}). Fix the error above and "
            "retry — the next ``openprogram`` will resume from where "
            "this left off."
        )

    _tty_write("  → npm run build\n")
    rc = subprocess.run(
        node_tool_cmd([npm, "run", "build", "--workspace", "apps/cli"]),
        cwd=str(repo_root),
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
    runtime_root = _managed_runtime_root()
    if runtime_root is not None:
        names = (
            ("node.exe", "node")
            if sys.platform == "win32"
            else ("node", "node.exe")
        )
        for name in names:
            bundled = runtime_root / "bin" / name
            if bundled.is_file():
                return str(bundled)
        raise RuntimeError(
            "the packaged Node.js runtime is missing. "
            "Reinstall the complete OpenProgram release."
        )
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
    the active profile's ``logs/ink-startup.log`` and the user would see a
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


def _has_interactive_tui_stdio() -> bool:
    """Return whether Ink can safely own an interactive terminal.

    POSIX startup may already have redirected fd 1 to a log, in which case
    the saved original stdout fd is the capability to inspect. Requiring both
    directions to be TTYs prevents ANSI frames from leaking into pipelines
    and sends scripted input directly to the line-oriented Rich fallback.
    """
    from openprogram import cli as _cli

    try:
        stdin_tty = bool(sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        stdin_tty = False

    try:
        stdout_tty = bool(sys.stdout.isatty())
    except (AttributeError, OSError, ValueError):
        stdout_tty = False

    saved_stdout = getattr(_cli, "_TUI_TTY_OUT", None)
    if not stdout_tty and saved_stdout is not None:
        try:
            stdout_tty = os.isatty(saved_stdout)
        except (OSError, TypeError, ValueError):
            stdout_tty = False

    return stdin_tty and stdout_tty


def _tui_first_frame_ready(path: Path) -> bool:
    """Return whether Node acknowledged a completed first Ink frame."""
    try:
        return path.read_text(encoding="utf-8") == _TUI_READY_MARKER
    except (OSError, UnicodeError):
        return False


def _is_tui_startup_failure(
    *, first_frame_ready: bool, user_interrupted: bool
) -> bool:
    """Classify failure using the explicit first-frame handshake."""
    return not user_interrupted and not first_frame_ready


def _tui_child_environment() -> dict[str, str]:
    """Return the child environment with Python's authoritative state root."""
    from openprogram.paths import get_state_dir

    env = os.environ.copy()
    # This is a private parent/child handshake. Never inherit a caller-supplied
    # path; _run_ink_child replaces it with a fresh path for every launch.
    env.pop(_TUI_READY_ENV, None)
    env["OPENPROGRAM_STATE_DIR"] = str(get_state_dir())
    return env


def _print_no_worker_hint() -> None:
    """Tell the user how to start a worker. Always writes to the
    saved original tty, so the message survives the TUI startup
    stdio redirect.
    """
    _tty_write(
        "openprogram: the background service isn't running and couldn't be\n"
        "started automatically (the port may be in use).\n"
        "\n"
        "  openprogram status                 # check what's holding the port\n"
        "  openprogram worker install         # install as a login service\n"
        "\n"
        "Then re-run `openprogram`.\n"
    )


def _resolve_worker_port(*, autostart: bool) -> int | None:
    """Find a live webui port, optionally starting a worker if none.

    Three sources, in order:

    1. A managed worker (``worker.lock`` + ``worker.port``). The
       well-supported path; ``worker stop`` / ``restart`` know about it.
    2. An unmanaged webui — i.e. a foreground ``openprogram web``
       process that the user launched themselves. Doesn't write the
       lock files, but ``find_running_webui()`` discovers it via a
       TCP probe on the default port. The TUI can talk to it just
       fine (same WS protocol).
    3. ``autostart=True`` and nothing is up — spawn a detached
       worker and wait briefly for it to start.

    Returns the port number on success, ``None`` on failure (caller
    prints the "no worker" hint).
    """
    from openprogram.worker import current_worker_pid, spawn_detached
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
            "If you have ``openprogram web`` running in another terminal,\n"
            "that webui is what the TUI should connect to — but its port\n"
            "wasn't detected. Stop it and either rerun `openprogram` (TUI\n"
            "will auto-start a managed worker) or run\n"
            "``openprogram worker start`` first.\n"
        )
        return None
    # Windows receives a longer Defender cold-scan allowance through the
    # compatibility seam; POSIX fails in a bounded time. Once a managed PID
    # has appeared, its disappearance is definitive startup failure and there
    # is no reason to wait out either deadline.
    deadline = time.monotonic() + tui_worker_ready_timeout_seconds()
    observed_pid: int | None = None
    while time.monotonic() < deadline:
        live_pid = current_worker_pid()
        if live_pid is not None:
            observed_pid = live_pid
        elif observed_pid is not None:
            return None
        port, _pid, source = find_running_webui()
        if source != "none" and port is not None:
            if _wait_until_listening(port, timeout=0.5):
                return port
        time.sleep(0.1)
    return None


def _restore_tty_stdio(tty_out: int | None, tty_err: int | None) -> None:
    """Restore process stdio from saved terminal descriptors."""
    for std_fd, saved in ((1, tty_out), (2, tty_err)):
        if saved is None:
            continue
        try:
            os.dup2(saved, std_fd)
        except OSError:
            pass


def _release_owned_tty_fds(tty_out: int | None, tty_err: int | None) -> None:
    """Restore stdio and close only descriptors created by this launcher."""
    _restore_tty_stdio(tty_out, tty_err)
    for saved in (tty_out, tty_err):
        if saved is None:
            continue
        try:
            os.close(saved)
        except OSError:
            pass


def _run_ink_child(
    *,
    node: str,
    entry: Path,
    port: int,
    tty_out: int | None,
    tty_err: int | None,
    agent,
    session_id: str | None,
    no_alt_screen: bool,
    screen_reader: bool,
) -> int:
    """Verify the endpoint, run Node, and return its exit status."""
    from openprogram.backend_endpoint import (
        OwnerAuthError,
        resolve_backend_endpoint,
    )

    env = _tui_child_environment()
    try:
        endpoint = resolve_backend_endpoint()
    except OwnerAuthError as exc:
        _tty_write(
            f"openprogram: cannot verify the Web server on port {port}: {exc}\n"
            "  Try `openprogram status`, or stop it and start again.\n"
        )
        raise SystemExit(2) from exc
    if endpoint.port != port:
        _tty_write(
            f"openprogram: the verified Web server is on port {endpoint.port}, "
            f"not {port}.\n  Try `openprogram status`.\n"
        )
        raise SystemExit(2)

    ws_url = endpoint.websocket_url
    env["OPENPROGRAM_BACKEND_URL"] = endpoint.base_url
    env["OPENPROGRAM_BACKEND_ORIGIN"] = endpoint.origin
    env["OPENPROGRAM_BACKEND_TOKEN"] = endpoint.token
    env["OPENPROGRAM_WS"] = ws_url
    if agent is not None and getattr(agent, "id", None):
        env["OPENPROGRAM_AGENT"] = agent.id
    if session_id:
        env["OPENPROGRAM_CONV"] = session_id

    cmd = [node, str(entry), "--ws", ws_url]
    if no_alt_screen:
        cmd.append("--no-alt-screen")
    if screen_reader:
        cmd.append("--screen-reader")

    launched_at = time.monotonic()
    user_interrupted = False
    with tempfile.TemporaryDirectory(prefix="openprogram-tui-ready-") as ready_dir:
        ready_path = Path(ready_dir) / "first-frame"
        env[_TUI_READY_ENV] = str(ready_path)

        # stdin=None preserves the native console/PTY capability. Passing fd 0
        # explicitly makes some Windows ConPTY launches appear non-interactive
        # to Node; POSIX naturally inherits fd 0 through the same path.
        proc = subprocess.Popen(cmd, env=env, stdout=tty_out, stderr=tty_err)
        try:
            proc.wait()
        except KeyboardInterrupt:
            user_interrupted = True
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        first_frame_ready = _tui_first_frame_ready(ready_path)

    elapsed = time.monotonic() - launched_at
    rc = proc.returncode or 0
    if _is_tui_startup_failure(
        first_frame_ready=first_frame_ready,
        user_interrupted=user_interrupted,
    ):
        _tty_write(
            "\n"
            f"openprogram: Ink TUI exited rc={rc} before its first frame "
            f"(after {elapsed:.2f}s).\n"
            "  The terminal could not complete raw input and renderer setup\n"
            "  (for example a revoked SSH PTY or Windows Git Bash / MinTTY).\n"
            "  Falling back to the Rich REPL (text-only chat).\n"
            "  Use the browser UI instead with: openprogram web\n"
            "\n"
        )
        # Global saved descriptors belong to openprogram.cli and remain open;
        # launcher-owned descriptors are closed by run_ink_tui's finally.
        _restore_tty_stdio(tty_out, tty_err)
        raise RuntimeError(
            f"Ink TUI exited before its first frame (rc={rc}, "
            f"after {elapsed:.2f}s). "
            "Falling back to the Rich REPL."
        )
    return rc


def run_ink_tui(
    *,
    agent=None,
    session_id: str | None = None,
    rt=None,
    no_alt_screen: bool = False,
    screen_reader: bool = False,
) -> None:
    """Connect the Node TUI to the live worker.

    The Node front-end discovers the default agent over WebSocket. A supplied
    session_id is passed through OPENPROGRAM_CONV to restore that session;
    otherwise the first message creates a session.
    """
    if not _has_interactive_tui_stdio():
        raise RuntimeError(
            "the Ink TUI requires terminal stdin and stdout; "
            "falling back to the Rich REPL for piped or redirected input"
        )

    # Resolve the Node binary + the built Ink bundle. Both errors are
    # actionable for the user but trivial to surface invisibly — at
    # this point ``cli._maybe_redirect_for_tui`` has already pointed
    # stderr at the active profile's ``logs/ink-startup.log``, so an
    # uncaught FileNotFoundError would land there and the user would
    # see ``openprogram`` exit with no output. Route them through
    # ``_tty_write`` instead.
    try:
        node = _resolve_node()
    except RuntimeError as e:
        _tty_write(f"openprogram: {e}\n")
        raise RuntimeError(str(e)) from e
    try:
        entry = _resolve_cli_entry()
    except (FileNotFoundError, RuntimeError) as e:
        # ``_resolve_cli_entry`` auto-builds the Ink bundle when
        # missing, so reaching here means either ``apps/cli/`` is gone (wheel
        # install without source) or the npm install / build failed.
        # Either way, the inner error string already explains it; just
        # add the bail-out options.
        _tty_write(
            f"openprogram: {e}\n\n"
            "Alternatives that don't need the TUI:\n\n"
            "  openprogram web               # browser UI\n"
            "  openprogram --print \"...\"     # one-shot prompt\n"
            "  openprogram --print \"hi\"      # one-shot prompt\n"
        )
        raise RuntimeError(str(e)) from e

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
        raise RuntimeError("the background service is unavailable")

    # application.main already did the post-parse dup2 for the TUI path and
    # stashed the
    # original tty fds on the cli module. Reuse those so the Node child
    # gets a clean terminal while logs land in ~/.openprogram/logs/.
    from openprogram import cli as _cli
    direct_stdio = tui_child_requires_direct_stdio_inheritance()
    tty_out = None if direct_stdio else getattr(_cli, "_TUI_TTY_OUT", None)
    tty_err = None if direct_stdio else getattr(_cli, "_TUI_TTY_ERR", None)
    owns_tty_fds = False
    if not direct_stdio and (tty_out is None or tty_err is None):
        from openprogram.paths import get_logs_dir

        log_dir = get_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "ink-server.log"
        owned_out: int | None = None
        owned_err: int | None = None
        try:
            owned_out = os.dup(1)
            owned_err = os.dup(2)
            tty_out, tty_err = owned_out, owned_err
            owns_tty_fds = True
            log_fd = os.open(
                str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND
            )
            try:
                os.dup2(log_fd, 1)
                os.dup2(log_fd, 2)
            finally:
                os.close(log_fd)
        except BaseException:
            # Allocation/redirection can itself fail (closed SSH PTY, fd
            # exhaustion). Restore and release whichever duplicates exist.
            _release_owned_tty_fds(owned_out, owned_err)
            raise

    try:
        rc = _run_ink_child(
            node=node,
            entry=entry,
            port=port,
            tty_out=tty_out,
            tty_err=tty_err,
            agent=agent,
            session_id=session_id,
            no_alt_screen=no_alt_screen,
            screen_reader=screen_reader,
        )
    finally:
        # The module-level descriptors are owned by openprogram.cli and may be
        # needed by its Rich fallback. Only close the emergency duplicates
        # created in this function, on every success and exception path.
        if owns_tty_fds:
            _release_owned_tty_fds(tty_out, tty_err)
    sys.exit(rc)
