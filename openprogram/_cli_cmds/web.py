"""``openprogram web`` handler — start the backend AND the Next.js frontend.

Historically ``openprogram web`` only started the FastAPI backend on
:8109; the Next.js dev server on :3000 (which serves the actual UI and
proxies ``/api`` + ``/ws`` back to :8109) had to be launched by hand with
``cd web && npm run dev``. That split is the source of the recurring
"only the backend came up / page won't open" confusion, so this command
now brings up both — the single ``openprogram web`` is the whole UI.

The frontend is auto-started only for a source checkout (the ``web/``
dir with ``node_modules`` sits next to the package — true for an editable
install). It is skipped when :3000 is already serving, when ``web/`` /
``node_modules`` is absent (a plain ``pip install``), or when
``OPENPROGRAM_WEB_NO_FRONTEND`` is set.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

# The Next.js dev server port. Matches every ``/api`` + ``/ws`` proxy
# note in webui/server.py — the frontend lives on :3000.
_FRONTEND_PORT = 3000


def _port_in_use(port: int) -> bool:
    """True when something is already listening on ``localhost:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_web_dir() -> Path | None:
    """Locate the ``web/`` source dir that ships next to the package.

    For an editable install ``openprogram/`` and ``web/`` are siblings
    under the repo root, so ``<pkg>/../web`` resolves it. Returns None
    when it (or its ``node_modules``) is missing — i.e. a plain wheel
    install with no frontend source to run.
    """
    try:
        import openprogram
        web = Path(openprogram.__file__).resolve().parent.parent / "web"
    except Exception:
        return None
    if (web / "package.json").exists() and (web / "node_modules").is_dir():
        return web
    return None


def _start_frontend() -> subprocess.Popen | None:
    """Spawn ``npm run dev`` for the Next.js frontend, or return None.

    Skipped when explicitly disabled, when :3000 is already serving (the
    user — or a previous run — already has it up), or when there's no
    runnable ``web/`` source. The child is put in its own process group
    / job so the whole ``npm → next`` tree can be torn down together on
    exit (see ``_stop_frontend``).
    """
    if os.environ.get("OPENPROGRAM_WEB_NO_FRONTEND"):
        return None
    if _port_in_use(_FRONTEND_PORT):
        print(f"Frontend already running at http://localhost:{_FRONTEND_PORT}")
        return None
    web = _find_web_dir()
    if web is None:
        return None

    import shutil
    npm = shutil.which("npm")
    if not npm:
        print("npm not found on PATH — start the frontend manually: "
              "cd web && npm run dev")
        return None

    # Own process group (POSIX) / new group (Windows) so a Ctrl+C to the
    # parent doesn't race-signal the child mid-spawn and so the whole
    # tree is killable on teardown.
    kwargs: dict = {"cwd": str(web)}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen([npm, "run", "dev"], **kwargs)
    print(f"Starting frontend (npm run dev) at http://localhost:{_FRONTEND_PORT} …")
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


def _cmd_web(port, open_browser):
    """Start the web UI (backend + frontend).

    ``port=None`` / ``open_browser=None`` means "use the user's stored
    UI pref" (written by ``openprogram setup ui``), falling back to
    the legacy defaults if none set.
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
        port = 8109
    if open_browser is None:
        open_browser = True

    # Start the backend WITHOUT opening a browser — the real UI is the
    # frontend on :3000, not the backend on :8109 (which has no HTML
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

    frontend = _start_frontend()
    # The UI lives on :3000 when a frontend is (or was already) up;
    # otherwise the backend port is the only thing serving.
    has_frontend = frontend is not None or _port_in_use(_FRONTEND_PORT)
    ui_url = (
        f"http://localhost:{_FRONTEND_PORT}" if has_frontend
        else f"http://localhost:{port}"
    )

    if open_browser:
        import threading

        def _open():
            import time
            import webbrowser
            # Give the frontend a moment to bind :3000 before opening.
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
