"""The link between the core and the desktop windows a client keeps open.

The core opens, resolves, captures and closes desktop Pages, and sends
commands to one exact desktop connection, without knowing which interface
carries them. An interface that keeps desktop connections — the Server's
WebSocket ``webtab`` control plane — registers itself here with
:func:`register`; every core call goes through the functions below.

With no interface registered there is no desktop link: lookups report
nothing and requests return the same ``ok: False`` results an interface
returns while no desktop window is connected.

Inside an agentic subprocess (``OPENPROGRAM_IN_AGENTIC_SUBPROCESS=1``) the
renderer bindings live in the parent worker. The child forwards Page
requests to the parent through the bridge it installs with
:func:`install_parent_bridge`; the parent answers them with its own link.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Protocol

RESPONSE_TIMEOUT_REASON_CODE = "desktop_response_timeout"

_NO_CLIENTS = "no WS clients connected (desktop shell not open?)"
_NO_CONNECTION = "originating desktop connection is unavailable"


class DesktopLink(Protocol):
    """The operations an interface implements for the core.

    ``ws`` values are the interface's own connection objects; the core only
    passes them back. Binding identities and revisions are documented on the
    Server's implementation (``ws_actions/webtab.py``).
    """

    def connections(self) -> list[Any]: ...
    def send(self, ws: Any, payload: str) -> bool: ...
    def request(self, command: dict, timeout: float) -> dict: ...
    def request_on_ws(self, ws: Any, command: dict, timeout: float = 5.0) -> dict: ...
    def registered_desktop_windows(self) -> list[tuple[Any, str, int]]: ...
    def ensure_connection_revision(self, ws: Any) -> int: ...
    def register_binding(self, ws: Any, window_id: str, tab_id: str, target_id: str,
                         **options: Any) -> str: ...
    def release_binding(self, binding_id: str) -> None: ...
    def binding_owner_revision(self, binding_id: str) -> tuple[Any, int] | None: ...
    def binding_page_key(self, binding_id: str) -> str: ...
    def binding_page_descriptor(self, binding_id: str) -> dict[str, Any]: ...
    def binding_revisions(self, binding_id: str) -> dict[str, int]: ...
    def binding_connection(self, binding_id: str) -> Any: ...
    def page_connections(self, page_key: str, window_id: str = "") -> list[Any]: ...
    def socket_tab_page_keys(self, ws: Any, window_id: str,
                             tab_id: str) -> tuple[int, list[str]] | None: ...
    def request_bound_tab(self, binding_id: str, **options: Any) -> dict: ...
    def request_bound_screenshot(self, binding_id: str, **options: Any) -> dict: ...
    def request_page_inventory(self, binding_id: str, timeout: float = 5.0) -> dict: ...
    def request_close_tab(self, binding_id: str, timeout: float = 5.0) -> dict: ...
    def request_open_tab(self, url: str, timeout: float = 15.0, *,
                         session_id: str | None = None) -> dict: ...


_link: DesktopLink | Any | None = None
_parent_request: Callable[[dict, float], dict] | None = None


def register(link: DesktopLink | Any) -> None:
    """Make ``link`` the desktop link of this process (the last one wins)."""
    global _link
    _link = link


def unregister(link: DesktopLink | Any | None = None) -> None:
    """Drop the registered link, or only ``link`` when it is the current one."""
    global _link
    if link is None or _link is link:
        _link = None


def current() -> DesktopLink | Any | None:
    """The registered link, or ``None`` when no interface keeps desktop windows."""
    return _link


def install_parent_bridge(request: Callable[[dict, float], dict] | None) -> None:
    """Route this subprocess's desktop requests to its parent worker."""
    global _parent_request
    _parent_request = request


def _in_subprocess() -> bool:
    return os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1"


def _unavailable() -> dict:
    return {
        "ok": False,
        "error": "surface binding is unavailable",
        "reason_code": "page_context_stale",
    }


def validated_open_ownership(value: Any) -> dict[str, bool]:
    """Return a trusted created/reused pair, or no ownership evidence."""
    if not isinstance(value, dict):
        return {}
    created = value.get("created")
    reused = value.get("reused")
    if type(created) is not bool or type(reused) is not bool or created == reused:
        return {}
    return {"created": created, "reused": reused}


# Connections


def connections() -> list[Any]:
    """Every live client connection, desktop window or not."""
    return list(_link.connections()) if _link is not None else []


def send(ws: Any, payload: str) -> bool:
    """Send one already-rendered frame to exactly ``ws``; False when it is gone."""
    return bool(_link.send(ws, payload)) if _link is not None else False


def registered_desktop_windows() -> list[tuple[Any, str, int]]:
    """Registered desktop windows as ``(connection, window_id, revision)``."""
    return _link.registered_desktop_windows() if _link is not None else []


def ensure_connection_revision(ws: Any) -> int:
    """The connection generation used to reject late async results."""
    return _link.ensure_connection_revision(ws) if _link is not None else 0


def page_connections(page_key: str, window_id: str = "") -> list[Any]:
    """Connections that own ``page_key`` or show ``window_id``."""
    return _link.page_connections(page_key, window_id) if _link is not None else []


def socket_tab_page_keys(ws: Any, window_id: str, tab_id: str) -> tuple[int, list[str]] | None:
    """The connection revision and live Page keys bound to one tab of ``ws``.

    ``None`` when ``ws`` has not registered ``window_id``.
    """
    return _link.socket_tab_page_keys(ws, window_id, tab_id) if _link is not None else None


# Requests


def request(command: dict, timeout: float) -> dict:
    """Send one command to whichever desktop shell answers first."""
    if _parent_request is not None:
        return _parent_request(command, timeout)
    if _link is None:
        return {"ok": False, "error": _NO_CLIENTS}
    return _link.request(command, timeout)


def request_on_ws(ws: Any, command: dict, *args: Any, **kwargs: Any) -> dict:
    """Send one command only to ``ws`` and wait for its reply (``timeout`` defaults to 5 s)."""
    if _link is None:
        return {"ok": False, "error": _NO_CONNECTION}
    return _link.request_on_ws(ws, command, *args, **kwargs)


def request_active_tab(timeout: float = 5.0) -> dict:
    """Return the currently visible desktop web tab, if one is active."""
    return request({"op": "active"}, timeout)


def _option(args: tuple, kwargs: dict, name: str, default: Any) -> Any:
    return args[0] if args else kwargs.get(name, default)


def request_open_tab(url: str, *args: Any, **kwargs: Any) -> dict:
    """Open/focus ``url`` and return the active desktop tab identity.

    Takes ``timeout`` (15 s) and the owning ``session_id``.
    """
    if _in_subprocess():
        from openprogram.state.execution_facts import current_session_id

        owner = kwargs.get("session_id") or current_session_id()
        command = {"op": "open", "url": url}
        if owner:
            command["session_id"] = owner
        return request(command, _option(args, kwargs, "timeout", 15.0))
    if _link is None:
        return {"ok": False, "error": _NO_CLIENTS}
    return _link.request_open_tab(url, *args, **kwargs)


def _revision_fields(command: dict, kwargs: dict) -> dict:
    for name in (
        "expected_page_revision",
        "expected_access_revision",
        "expected_geometry_revision",
    ):
        if kwargs.get(name):
            command[name] = kwargs[name]
    return command


def request_bound_tab(binding_id: str, **kwargs: Any) -> dict:
    """Resolve the exact bound Page without requiring OS foreground state.

    Takes ``url``, ``timeout`` (5 s) and the ``expected_page_revision``,
    ``expected_access_revision`` and ``expected_geometry_revision`` guards.
    """
    if _in_subprocess():
        command = {"op": "activate", "binding_id": binding_id}
        if kwargs.get("url"):
            command["url"] = kwargs["url"]
        return request(_revision_fields(command, kwargs), kwargs.get("timeout", 5.0))
    if _link is None:
        return _unavailable()
    return _link.request_bound_tab(binding_id, **kwargs)


def request_bound_screenshot(binding_id: str, **kwargs: Any) -> dict:
    """Capture one exact Page without revealing the view.

    Takes ``timeout`` (5 s) and the same revision guards as
    :func:`request_bound_tab`.
    """
    if _in_subprocess():
        return request(_revision_fields(
            {"op": "screenshot", "binding_id": binding_id}, kwargs,
        ), kwargs.get("timeout", 5.0))
    if _link is None:
        return _unavailable()
    return _link.request_bound_screenshot(binding_id, **kwargs)


def request_page_inventory(binding_id: str, *args: Any, **kwargs: Any) -> dict:
    """List every Page owned by the same desktop window as one binding."""
    if _link is None:
        return _unavailable()
    return _link.request_page_inventory(binding_id, *args, **kwargs)


def request_close_tab(binding_id: str, *args: Any, **kwargs: Any) -> dict:
    """Close the desktop tab owned by one live binding (``timeout`` 5 s)."""
    if _in_subprocess():
        return request(
            {"op": "close", "binding_id": binding_id},
            _option(args, kwargs, "timeout", 5.0),
        )
    if _link is None:
        return _unavailable()
    return _link.request_close_tab(binding_id, *args, **kwargs)


# Bindings


def register_binding(ws: Any, window_id: str, tab_id: str, target_id: str, **options: Any) -> str:
    """Bind one exact desktop Page and return its binding id."""
    if _link is None:
        raise RuntimeError("no desktop link is registered")
    return _link.register_binding(ws, window_id, tab_id, target_id, **options)


def release_binding(binding_id: str) -> None:
    if _link is not None:
        _link.release_binding(binding_id)


def binding_owner_revision(binding_id: str) -> tuple[Any, int] | None:
    return _link.binding_owner_revision(binding_id) if _link is not None else None


def binding_page_key(binding_id: str) -> str:
    return _link.binding_page_key(binding_id) if _link is not None else ""


def binding_page_descriptor(binding_id: str) -> dict[str, Any]:
    return _link.binding_page_descriptor(binding_id) if _link is not None else {}


def binding_revisions(binding_id: str) -> dict[str, int]:
    return _link.binding_revisions(binding_id) if _link is not None else {}


def binding_connection(binding_id: str) -> Any:
    return _link.binding_connection(binding_id) if _link is not None else None
