"""``openprogram web`` handler — start the single-port worker (whole UI).

Single-port architecture: the FastAPI worker serves the API, ``/ws`` AND
the Next.js static export (``apps/web/out/``) on one port, so this command
just spawns the detached worker and opens the browser at that port.

The detached worker owns the exported frontend and the API on the same port.
"""
from __future__ import annotations

import os
import sys

from openprogram._ports import (
    backend_is_ours as _backend_is_ours,
    port_in_use as _port_in_use,
    port_owner_hint,
)

# The default single-port Web server address.
_FRONTEND_PORT = 18100


def _browser_url(port: int) -> str:
    """The URL a browser should open for the active Web server.

    ``http://localhost:<port>`` is only right for a loopback bind. When
    the server binds a LAN address, a VPN address, or sits behind an
    HTTPS proxy, its effective Origins say so and localhost is not among
    them — opening it lands the user on a dead page, and minting a token
    URL for it fails outright. Ask the live snapshot instead, and fall
    back to localhost only when there is no snapshot to ask.
    """
    from openprogram.backend_endpoint import (
        OwnerAuthError,
        read_active_web_access,
        select_request_origin,
    )

    try:
        active_access = read_active_web_access()
        if active_access.port == int(port):
            return select_request_origin(active_access)
    except OwnerAuthError:
        pass
    return f"http://localhost:{port}"


def _active_owner_auth_url(base_url: str, port: int) -> str:
    """Return a bootstrap URL only when ``base_url`` is an effective Origin."""
    from openprogram._ports import backend_accepts_owner_challenge
    from openprogram.backend_endpoint import (
        build_owner_auth_url,
        read_active_web_access,
        read_web_token,
    )
    from openprogram.backend_endpoint import OwnerAuthError

    if _backend_is_ours(port) is not True:
        raise OwnerAuthError("active Web server is not owned by this profile")
    active_access = read_active_web_access()
    if active_access.port != port:
        raise OwnerAuthError("active Web port does not match the worker port")
    # The token rides in the fragment, which the *page* that loads can
    # read. Being ours on loopback does not make ``base_url`` ours: a
    # configured DNS name or proxy Origin may resolve somewhere else
    # entirely. Challenge that exact URL before minting a token for it.
    if not backend_accepts_owner_challenge(port, origin=base_url):
        raise OwnerAuthError(f"{base_url} did not prove it is this Web server")
    return build_owner_auth_url(
        base_url,
        token=read_web_token(),
        effective_origins=active_access.effective_origins,
    )


def _cmd_web_auth_url(base_url: str) -> int:
    """Print a fragment-bootstrap URL for the active Web server."""
    from openprogram.worker.lifecycle import read_worker_port
    from openprogram.backend_endpoint import OwnerAuthError

    port = read_worker_port()
    if port is None:
        print("error: no active OpenProgram Web server", file=sys.stderr)
        return 1
    try:
        url = _active_owner_auth_url(base_url, port)
    except OwnerAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(url)
    return 0


def _cmd_web(web_port: int | None, open_browser: bool | None) -> None:
    """Start the web UI (single port — API, /ws and the frontend export).

    ``web_port`` / ``open_browser`` = None means "use the user's stored
    pref" (``openprogram ports`` / ``openprogram setup ui``). Port
    resolution: explicit ``--web-port`` → env / pref / default via
    ``resolve_worker_port``.
    """
    try:
        from openprogram.webui import start_web
    except ImportError:
        print("Web UI dependencies are missing from this installation.")
        print("Reinstall the complete OpenProgram release.")
        sys.exit(1)

    if open_browser is None:
        try:
            from openprogram.setup import read_ui_prefs
            open_browser = read_ui_prefs()["open_browser"]
        except Exception:
            pass
    if open_browser is None:
        open_browser = True

    from openprogram.worker.lifecycle import resolve_worker_port
    port = web_port = int(web_port) if web_port else resolve_worker_port()

    # Backend port already held? Binding again raises a bare errno-48
    # traceback, so detect it up front — but distinguish OUR backend
    # already running from an unrelated program squatting the port. A
    # bare ``connect`` can't tell them apart and would mislabel a
    # squatter as "already running", then open a browser at it.
    if _port_in_use(port):
        ours = _backend_is_ours(port)
        if ours is True:
            ui = _browser_url(port)
            print(f"openprogram web is already running (port {port} in use).")
            print(f"  Open the UI:  {ui}")
            print("  Or stop the other instance first:  pkill -f 'openprogram web'")
            if open_browser:
                from openprogram._compat import open_browser_url
                from openprogram.backend_endpoint import OwnerAuthError

                try:
                    if not open_browser_url(_active_owner_auth_url(ui, port)):
                        print("  Browser not opened: no graphical browser is available")
                except OwnerAuthError as exc:
                    print(f"  Browser not opened: {exc}")
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

    # Start the worker as a DETACHED background service, then free the
    # terminal — same machinery the TUI path already uses
    # (cli/ink.py:_resolve_worker_port → spawn_detached). ``worker run``
    # serves API + /ws + the frontend export on the single port
    # (runner.py start_web + webui/frontend.py), so we don't boot it
    # in-process here. This is why closing the terminal doesn't kill the
    # web UI — the worker has no controlling TTY and survives. Stop it
    # with ``openprogram stop``.
    from openprogram.worker import spawn_detached

    # Honour an explicit --port / --web-port on the detached path: the
    # worker resolves its port from this env (lifecycle.resolve_worker_port),
    # not from function args.
    # Publish the already-resolved effective port unconditionally.  In
    # particular, an explicit CLI flag must override a stale inherited env
    # value; otherwise the parent waits on one port while the worker binds
    # another.
    os.environ["OPENPROGRAM_WEB_PORT"] = str(web_port)

    rc = spawn_detached()
    if rc != 0:
        print("openprogram: couldn't start the background service "
              "(the port may be in use). Try `openprogram status`.")
        sys.exit(1)

    # Wait briefly for the frontend to bind before opening the browser, so
    # the user doesn't land on a connection-refused page.
    try:
        from openprogram.cli.ink import _wait_until_listening
        _wait_until_listening(web_port, timeout=10.0)
    except Exception:
        pass

    # Only now: the access snapshot naming the real browser Origin is
    # written by the server as it binds, so reading it earlier would
    # always miss and fall back to localhost.
    ui_url = _browser_url(web_port)

    if open_browser:
        from openprogram._compat import open_browser_url
        from openprogram.backend_endpoint import OwnerAuthError

        try:
            if not open_browser_url(_active_owner_auth_url(ui_url, web_port)):
                print("Browser not opened: no graphical browser is available")
        except OwnerAuthError as exc:
            print(f"Browser not opened: {exc}")

    print(f"Web UI: {ui_url}")
    print("Running in the background — close this terminal any time.")
    print("Stop it with:  openprogram stop")
