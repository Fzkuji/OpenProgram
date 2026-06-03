"""``openprogram web`` handler — start the backend AND the Next.js frontend.

Historically ``openprogram web`` only started the FastAPI backend on
:18109; the Next.js dev server on :18100 (which serves the actual UI and
proxies ``/api`` + ``/ws`` back to :18109) had to be launched by hand with
``cd web && npm run dev``. That split is the source of the recurring
"only the backend came up / page won't open" confusion, so this command
now brings up both — the single ``openprogram web`` is the whole UI.

The frontend is auto-started only for a source checkout (the ``web/``
dir with ``node_modules`` sits next to the package — true for an editable
install). It is skipped when :18100 is already serving, when ``web/`` /
``node_modules`` is absent (a plain ``pip install``), or when
``OPENPROGRAM_WEB_NO_FRONTEND`` is set.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from openprogram._ports import (
    backend_is_ours as _backend_is_ours,
    frontend_is_ours as _frontend_is_ours,
    port_in_use as _port_in_use,
    port_owner_hint,
)

# The Next.js dev server port. Matches every ``/api`` + ``/ws`` proxy
# note in webui/server.py — the frontend lives on :18100.
_FRONTEND_PORT = 18100


def _find_web_dir() -> Path | None:
    """Locate the ``web/`` source dir that ships next to the package.

    For an editable install ``openprogram/`` and ``web/`` are siblings
    under the repo root, so ``<pkg>/../web`` resolves it. Returns None
    only when there is no ``web/`` source at all (a plain wheel install).

    Note: a missing ``node_modules`` is NOT treated as "no web dir" here
    — ``_start_frontend`` checks the deps separately so it can print an
    actionable ``npm install`` hint instead of silently doing nothing.
    """
    try:
        import openprogram
        web = Path(openprogram.__file__).resolve().parent.parent / "web"
    except Exception:
        return None
    if (web / "package.json").exists():
        return web
    return None


def _frontend_command(web: Path, web_port: int) -> list[str] | None:
    """Command to run the Next.js dev server on the PINNED port.

    Invokes the project-local ``next`` binary DIRECTLY instead of going
    through ``npm run dev``. ``npm run`` relies on injecting
    ``node_modules/.bin`` into a sub-shell's PATH; when the spawning
    environment is even slightly unusual that injection misfires and the
    script dies with ``sh: next: command not found`` (the recurring bug).
    Running ``node node_modules/next/dist/bin/next`` needs nothing on
    PATH but ``node`` itself, and works identically on Windows (no
    ``.cmd`` exec quirk).

    ``--port`` is pinned so the dev server can never silently bump to
    :18101 when :18100 is taken — the URL every ``/api`` + ``/ws`` proxy
    assumes stays fixed. Returns None when node / the frontend deps
    aren't installed.
    """
    import shutil
    node = shutil.which("node")
    next_js = web / "node_modules" / "next" / "dist" / "bin" / "next"
    next_bin = web / "node_modules" / ".bin" / "next"
    pinned = ["dev", "--turbo", "--port", str(web_port)]
    if node and next_js.exists():
        return [node, str(next_js), *pinned]
    if next_bin.exists():
        # Fallback: the .bin shim (POSIX). On Windows the next_js branch
        # above is taken, so this never hits the .cmd-exec problem.
        return [str(next_bin), *pinned]
    return None


def _start_frontend(backend_port: int, web_port: int | None = None) -> subprocess.Popen | None:
    """Spawn the Next.js dev server on the fixed port :18100, or return None.

    Robustness over the old ``npm run dev`` spawn:
      * runs the local ``next`` binary directly, so it can't fail with
        ``next: command not found``;
      * pins ``--port`` + ``PORT`` so the URL is deterministic;
      * points ``OPENPROGRAM_BACKEND_URL`` at the backend port we actually
        bound, so the ``/ws`` + ``/healthz`` rewrites in ``next.config``
        never fall back to the stale hard-coded default;
      * augments the child PATH with node + ``node_modules/.bin``;
      * prints an actionable message (instead of failing silently or with
        a traceback) when node / the frontend deps aren't installed.

    Skipped when explicitly disabled, when OUR frontend is already serving
    :18100, or when there's no ``web/`` source (a plain wheel install). The
    child is put in its own process group / job so the whole tree tears
    down together on exit (see ``_stop_frontend``).
    """
    if os.environ.get("OPENPROGRAM_WEB_NO_FRONTEND"):
        return None
    if web_port is None:
        web_port = _FRONTEND_PORT
    web = _find_web_dir()
    if web is None:
        return None  # no web/ source — backend only
    # Something on the frontend port already? Only reuse it when it actually
    # answers like our frontend — a bare ``connect`` would happily "reuse"
    # an unrelated program squatting it and desync every proxied URL.
    if _port_in_use(web_port):
        ours = _frontend_is_ours(web_port)
        if ours is True:
            print(f"Frontend already running at http://localhost:{web_port}")
            return None
        # Held, but it doesn't answer like our frontend (False) or doesn't
        # answer at all (None). Don't spawn a second dev server — Next would
        # bump to the next port and the fixed-port URL every proxy assumes
        # breaks.
        print(f"Port {web_port} is held by a process that does not look "
              f"like the openprogram frontend.")
        hint = port_owner_hint(web_port)
        if hint:
            print(hint)
        print(f"  Free it (e.g. `lsof -ti:{web_port} | xargs kill`) and "
              f"rerun, or set OPENPROGRAM_WEB_NO_FRONTEND=1 to skip the frontend.")
        return None

    import shutil
    if shutil.which("node") is None:
        print("Frontend not started: Node.js not found on PATH (needs Node 18+).\n"
              "  Install Node, or start the frontend manually: cd web && npm run dev")
        return None
    cmd = _frontend_command(web, web_port)
    if cmd is None:
        print("Frontend not started: dependencies are not installed.\n"
              f"  Run:  cd {web} && npm install")
        return None

    # Put node_modules/.bin + node's own dir first on the child's PATH so
    # next's node shebang and any child binaries resolve regardless of how
    # the parent process was launched (the env that broke ``npm run dev``).
    env = dict(os.environ)
    node_dir = str(Path(shutil.which("node")).parent)
    bin_dir = str(web / "node_modules" / ".bin")
    env["PATH"] = os.pathsep.join([bin_dir, node_dir, env.get("PATH", "")])
    env["PORT"] = str(web_port)
    # Pin the proxy target to the backend port we actually bound. Without
    # this, next.config's ``/ws`` + ``/healthz`` rewrites fall back to their
    # hard-coded default and the WebSocket connects to a dead port. ``/api``
    # is unaffected (its route handler reads ``worker.port`` per request),
    # but ``/ws`` is resolved once at boot, so it must be correct here.
    env.setdefault("OPENPROGRAM_BACKEND_URL", f"http://127.0.0.1:{backend_port}")

    kwargs: dict = {"cwd": str(web), "env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    print(f"Starting frontend on http://localhost:{web_port} …")
    return proc


def _stop_frontend(proc: subprocess.Popen | None) -> None:
    """Terminate the frontend subprocess tree, cross-platform."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            import signal
            # Kill the whole process group (npm + next) we created with
            # start_new_session=True.
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cmd_web(port, open_browser, web_port=None):
    """Start the web UI (backend + frontend).

    ``port`` / ``web_port`` / ``open_browser`` = None means "use the
    user's stored pref" (``openprogram ports`` / ``openprogram setup
    ui``), falling back to the defaults if none set. Resolution order for
    each port: explicit arg → env (``OPENPROGRAM_BACKEND_PORT`` /
    ``OPENPROGRAM_WEB_PORT``) → stored pref → module default.
    """
    try:
        from openprogram.webui import start_web
    except ImportError:
        print("Web UI dependencies not installed.")
        print("Install with: pip install openprogram[web]")
        sys.exit(1)

    if port is None or open_browser is None:
        try:
            from openprogram.setup import read_ui_prefs
            prefs = read_ui_prefs()
            if port is None:
                port = prefs["port"]
            if open_browser is None:
                open_browser = prefs["open_browser"]
        except Exception:
            pass
    if port is None:
        port = 18109
    if open_browser is None:
        open_browser = True

    # Frontend port: explicit --web-port > env > stored pref > default.
    if web_port is None:
        env_wp = os.environ.get("OPENPROGRAM_WEB_PORT")
        if env_wp:
            web_port = int(env_wp)
    if web_port is None:
        try:
            from openprogram.setup import read_ui_prefs
            web_port = read_ui_prefs().get("web_port")
        except Exception:
            pass
    web_port = int(web_port) if web_port else _FRONTEND_PORT

    # Backend port already held? Binding again raises a bare errno-48
    # traceback, so detect it up front — but distinguish OUR backend
    # already running from an unrelated program squatting the port. A
    # bare ``connect`` can't tell them apart and would mislabel a
    # squatter as "already running", then open a browser at it.
    if _port_in_use(port):
        ours = _backend_is_ours(port)
        if ours is True:
            ui = (f"http://localhost:{web_port}"
                  if _port_in_use(web_port) else f"http://localhost:{port}")
            print(f"openprogram web is already running (port {port} in use).")
            print(f"  Open the UI:  {ui}")
            print("  Or stop the other instance first:  pkill -f 'openprogram web'")
            if open_browser:
                import webbrowser
                webbrowser.open(ui)
            return
        # Held by something that is NOT an openprogram backend. The port is
        # pinned on purpose (a stable UI URL), so refuse with an actionable
        # message rather than silently drifting to another port or opening
        # a browser at a foreign service.
        print(f"Port {port} is in use by another process (not openprogram).")
        hint = port_owner_hint(port)
        if hint:
            print(hint)
        print(f"  Free it (e.g. `lsof -ti:{port} | xargs kill`), or pick a")
        print("  different backend port:  openprogram setup ui")
        sys.exit(1)

    # Start the backend WITHOUT opening a browser — the real UI is the
    # frontend on :18100, not the backend on :18109 (which has no HTML
    # routes). We open the correct URL ourselves once we know whether a
    # frontend is available.
    thread = start_web(port=port, open_browser=False)

    try:
        from openprogram.worker import current_worker_pid
        pid = current_worker_pid()
        if pid:
            print(f"Channels worker running (PID {pid}).")
    except Exception:
        pass

    frontend = _start_frontend(port, web_port)
    # The UI lives on the frontend port only when OUR frontend is up —
    # either we just spawned it, or _start_frontend reused one it confirmed
    # was ours. A bare _port_in_use(web_port) here would ALSO fire when an
    # unrelated program squats it (the squatter _start_frontend already
    # refused to reuse), and we'd then open the browser at that foreign one.
    has_frontend = frontend is not None or _frontend_is_ours(web_port) is True
    ui_url = (
        f"http://localhost:{web_port}" if has_frontend
        else f"http://localhost:{port}"
    )

    if open_browser:
        import threading

        def _open():
            import time
            import webbrowser
            # Give the frontend a moment to bind :18100 before opening.
            time.sleep(2.0 if frontend is not None else 0.5)
            webbrowser.open(ui_url)

        threading.Thread(target=_open, daemon=True).start()

    print(f"Web UI: {ui_url}")
    print("Press Ctrl+C to stop.")
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping web UI.")
    finally:
        _stop_frontend(frontend)
