"""Where the OpenProgram App's own browser listens for CDP.

The desktop shell (Electron) exposes a CDP endpoint (see
``apps/desktop/main/instance.js``); the tab driver attaches to it to operate
the App's web tabs.
"""
from __future__ import annotations

from typing import Optional

APP_DEBUG_PORT = 9223


def app_debug_port() -> int:
    """The CDP port of the App that serves this profile.

    The development App (profile ``dev``) listens on its own port, so its
    tab driver never attaches to the stable App's tabs.
    """
    from openprogram.operations.worker.dev_instance import DEV_APP_DEBUG_PORT, DEV_PROFILE
    from openprogram.state.paths import get_active_profile

    return DEV_APP_DEBUG_PORT if get_active_profile() == DEV_PROFILE else APP_DEBUG_PORT


def desktop_app_ws_url(timeout: float = 0.6) -> Optional[str]:
    """The App's browser-level ``webSocketDebuggerUrl``, or ``None`` while the App is not running.

    Electron's debugging service refuses the handshake path Playwright adds
    to an http endpoint (``GET /json/version/`` answers 400), so
    ``connect_over_cdp`` is given the ``ws://`` URL itself. The probe skips
    any configured proxy: a loopback request sent to one never arrives.
    """
    import json
    import urllib.request
    base = f"http://127.0.0.1:{app_debug_port()}"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{base}/json/version", timeout=timeout) as resp:
            return json.load(resp).get("webSocketDebuggerUrl")
    except Exception:
        return None


def attach_to_app(playwright, endpoint: str):
    """Attach Playwright to a running App and leave the App as it is.

    The only caller of ``connect_over_cdp``. By default Playwright treats the
    pages it adopts as a test browser: each one, the App's own window included,
    is told the system is in light mode and the page focused, and downloads go
    to Playwright's folder. ``no_defaults`` turns that off; a caller that needs
    one of them sets it on the page it operates.
    """
    return playwright.chromium.connect_over_cdp(endpoint, no_defaults=True)
