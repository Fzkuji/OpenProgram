"""``openprogram web`` handler — start Web UI."""
from __future__ import annotations

import sys


def _cmd_web(port, open_browser):
    """Start the web UI.

    ``port=None`` / ``open_browser=None`` means "use the user's stored
    UI pref" (written by ``openprogram config ui``), falling back to
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
        port = 8110
    if open_browser is None:
        open_browser = True

    thread = start_web(port=port, open_browser=open_browser)

    try:
        from openprogram.worker import current_worker_pid
        pid = current_worker_pid()
        if pid:
            print(f"Channels worker running (PID {pid}).")
    except Exception:
        pass

    print("Press Ctrl+C to stop.")
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping web UI.")
