"""``openprogram web`` handler — start the single-port worker (whole UI).

Single-port architecture: the FastAPI worker serves the API, ``/ws`` AND
the Next.js static export (``apps/web/out/``) on one port, so this command
just spawns the detached worker and opens the browser at that port.

The detached worker owns the exported frontend and the API on the same port.
"""
from __future__ import annotations

import logging
import os
import sys

from openprogram.host.worker.ports import (
    backend_is_ours as _backend_is_ours,
    port_in_use as _port_in_use,
    port_owner_hint,
)

_log = logging.getLogger(__name__)

# ``web auth-url`` exits with this when the port it was asked about is held
# by something that is not this profile's worker (another program, or another
# account's worker). The App reads it to say so instead of waiting.
EXIT_PORT_HELD = 3


def _browser_url(port: int) -> str:
    """The URL a browser should open for the active Web server.

    ``http://localhost:<port>`` is only right for a loopback bind. When
    the server binds a LAN address, a VPN address, or sits behind an
    HTTPS proxy, its effective Origins say so and localhost is not among
    them — opening it lands the user on a dead page, and minting a token
    URL for it fails outright. Ask the live snapshot instead, and fall
    back to localhost only when there is no snapshot to ask.
    """
    from openprogram.host.security.backend_endpoint import (
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
    from openprogram.host.security.backend_endpoint import backend_accepts_owner_challenge
    from openprogram.host.security.backend_endpoint import (
        build_owner_auth_url,
        read_active_web_access,
        read_web_token,
    )
    from openprogram.host.security.backend_endpoint import OwnerAuthError

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
    """Print a fragment-bootstrap URL for the active Web server.

    Exits with :data:`EXIT_PORT_HELD` and names the holder on stderr when
    this profile has no worker but something listens on ``base_url``'s port.
    """
    from urllib.parse import urlsplit

    from openprogram.operations.worker.lifecycle import current_worker_pid, read_worker_port
    from openprogram.host.security.backend_endpoint import OwnerAuthError

    port = read_worker_port()
    # This profile's worker between binding its port and naming it in
    # worker.port is starting, not another holder of the port.
    if port is None and current_worker_pid() is None:
        try:
            asked = urlsplit(base_url).port
        except ValueError:
            asked = None
        if asked is not None and _port_in_use(asked):
            owner = port_owner_hint(asked).strip().removeprefix("Held by — ")
            print(
                f"port {asked} is in use by "
                f"{owner or 'another program or account'}",
                file=sys.stderr,
            )
            return EXIT_PORT_HELD
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
        # Import probe: the Web UI server stack must be installed. The
        # ``openprogram.interfaces.server`` package itself imports neither.
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("Web UI dependencies are missing from this installation.")
        print("Reinstall the complete OpenProgram release.")
        sys.exit(1)

    if open_browser is None:
        try:
            from openprogram.state.preferences import read_ui_prefs
            open_browser = read_ui_prefs()["open_browser"]
        except Exception:
            # Falls back to the default open-browser behaviour.
            _log.debug("ui prefs unreadable", exc_info=True)
    if open_browser is None:
        open_browser = True

    from openprogram.operations.worker.lifecycle import (
        explicit_web_port,
        read_worker_port,
        resolve_worker_port,
    )

    if web_port:
        # An explicit --web-port is this run's setting; the worker resolves
        # its port from this env (lifecycle.claim_worker_port).
        os.environ["OPENPROGRAM_WEB_PORT"] = str(int(web_port))
    running = read_worker_port()
    port = running if running is not None else resolve_worker_port()

    # A listener on the port is ours only if this profile's worker proves
    # it; anything else (another program, another account's worker) is never
    # opened. A port the profile chose for itself moves when the worker
    # starts; a port the user set is refused with the holder named.
    if _port_in_use(port):
        ours = _backend_is_ours(port)
        if ours is True:
            ui = _browser_url(port)
            print(f"openprogram web is already running (port {port} in use).")
            print(f"  Open the UI:  {ui}")
            print("  Or stop it first:  openprogram stop")
            if open_browser:
                from openprogram.host._platform.environment import open_browser_url
                from openprogram.host.security.backend_endpoint import OwnerAuthError

                try:
                    if not open_browser_url(_active_owner_auth_url(ui, port)):
                        print("  Browser not opened: no graphical browser is available")
                except OwnerAuthError as exc:
                    print(f"  Browser not opened: {exc}")
            return
        explicit = explicit_web_port()
        if explicit is not None:
            print(f"Port {port} (set by {explicit[1]}) is in use by another program or account.")
            hint = port_owner_hint(port)
            if hint:
                print(hint)
            print("  Quit that program, or set another port:  openprogram setup ui")
            sys.exit(1)

    # Start the worker as a DETACHED background service, then free the
    # terminal — same machinery the TUI path already uses
    # (cli/ink.py:_resolve_worker_port → spawn_detached). ``worker run``
    # serves API + /ws + the frontend export on the single port
    # (runner.py start_web + webui/frontend.py), so we don't boot it
    # in-process here. This is why closing the terminal doesn't kill the
    # web UI — the worker has no controlling TTY and survives. Stop it
    # with ``openprogram stop``.
    from openprogram.operations.worker import spawn_detached

    rc = spawn_detached()
    if rc != 0:
        print("openprogram: couldn't start the background service "
              "(the port may be in use). Try `openprogram status`.")
        sys.exit(1)

    # Wait for the server to bind before opening the browser: the sign-in
    # link can only be minted once it answers, and a page opened without
    # it loads nothing. A first start can take well over a minute on
    # Windows (Defender scans the runtime), so wait as long as the TUI does.
    # The worker settles its port as it starts (it may move off a held one),
    # so wait for the port it names, not the one guessed here.
    print("Starting the background service…")
    listening = False
    try:
        import time

        from openprogram.host._platform.environment import tui_worker_ready_timeout_seconds
        from openprogram.interfaces.cli.ink import _wait_until_listening
        deadline = time.monotonic() + tui_worker_ready_timeout_seconds()
        named = read_worker_port()
        while named is None and time.monotonic() < deadline:
            time.sleep(0.2)
            named = read_worker_port()
        if named is not None:
            web_port = named
            listening = _wait_until_listening(named, timeout=max(0.0, deadline - time.monotonic()))
    except Exception:
        _log.debug("wait for the worker's port failed", exc_info=True)
    if not listening:
        print("The background service is still starting.")
        print("Run `openprogram web` again in a moment to open the page signed in.")
        print("Stop it with:  openprogram stop")
        return

    # Only now: the access snapshot naming the real browser Origin is
    # written by the server as it binds, so reading it earlier would
    # always miss and fall back to localhost.
    ui_url = _browser_url(web_port)

    if open_browser:
        from openprogram.host._platform.environment import open_browser_url
        from openprogram.host.security.backend_endpoint import OwnerAuthError

        try:
            if not open_browser_url(_active_owner_auth_url(ui_url, web_port)):
                print("Browser not opened: no graphical browser is available")
        except OwnerAuthError as exc:
            print(f"Browser not opened: {exc}")

    print(f"Web UI: {ui_url}")
    print("To open it again signed in, run `openprogram web`; the bare address")
    print("above shows an empty page until it is opened that way.")
    print("Running in the background — close this terminal any time.")
    print("Stop it with:  openprogram stop")
