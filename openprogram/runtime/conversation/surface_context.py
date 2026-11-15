"""Turn-scoped awareness of a visible OpenProgram desktop surface."""
from __future__ import annotations

import logging
import contextvars
import os
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

_log = logging.getLogger(__name__)


_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "openprogram_surface_context", default=None,
)

# How long list_pages waits for a Desktop window to (re)register before it
# reports desktop_unavailable. Covers the App reopening after a worker or App
# restart; an online window whose inventory fails is never waited on.
DESKTOP_RECONNECT_WAIT_SECONDS = 20.0
_DESKTOP_RECONNECT_POLL_SECONDS = 0.5


class DesktopUnavailableError(RuntimeError):
    """The Desktop window or binding that should answer is not connected."""

PAGE_CLEANUP_HANDOFF = (
    "Close the remaining background Page in OpenProgram, then continue the "
    "task manually or retry it."
)


def _binding_generation(binding_id: str) -> int:
    """The connection generation of a live binding's Page; 0 once the binding is gone."""
    from openprogram.host.resources import desktop_link
    return int(desktop_link.binding_page_descriptor(binding_id).get("connection_generation") or 0)


def page_cleanup_failure(error: str) -> dict:
    """Return the canonical result when an agent Page may remain open."""
    return {
        "ok": False,
        "status": "infeasible",
        "success": False,
        "infeasible_declared": True,
        "reason_code": "page_cleanup_failed",
        "error": error,
        "summary": (
            "The agent-created background Page could not be confirmed closed."
        ),
        "handoff_instruction": PAGE_CLEANUP_HANDOFF,
    }


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _revision(value: Any) -> int:
    try:
        return max(0, min(int(value), 2**63 - 1))
    except (TypeError, ValueError, OverflowError):
        return 0


def _descriptor(raw: dict) -> dict | None:
    if raw.get("version") != 1:
        return None
    window_id = _text(raw.get("window_id"), 160)
    tab_id = _text(raw.get("tab_id"), 512)
    if not window_id or not tab_id:
        return None
    region = raw.get("region") if raw.get("region") in {"left", "right", "center"} else "right"
    access = "enabled" if raw.get("access") == "enabled" else "disabled"
    return {
        "window_id": window_id,
        "tab_id": tab_id,
        "region": region,
        "access": access,
        "title": _text(raw.get("title"), 240),
        "origin": _origin(str(raw.get("url") or "")),
        "focused": bool(raw.get("focused")),
        "geometry_revision": _revision(raw.get("geometry_revision")),
    }


def _preview(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    landmarks = []
    for item in raw.get("aria_landmarks") or []:
        if not isinstance(item, dict) or len(landmarks) >= 12:
            continue
        landmarks.append({
            "role": _text(item.get("role"), 40),
            "name": _text(item.get("name"), 160),
        })
    try:
        interactive_count = max(0, min(int(raw.get("interactive_count") or 0), 10_000))
    except (TypeError, ValueError):
        interactive_count = 0
    return {
        "visible_text_excerpt": _text(raw.get("visible_text_excerpt"), 2_000),
        "text_truncated": bool(raw.get("text_truncated")),
        "aria_landmarks": landmarks,
        "landmarks_truncated": bool(raw.get("landmarks_truncated")),
        "interactive_count": interactive_count,
    }


def window_context(
    window_id: str = "",
    *,
    preferred_tab_id: str = "",
) -> dict:
    """Create a non-authorizing context that preserves one origin window."""
    normalized = _text(window_id, 160)
    return {
        "context_id": "surface_ctx_" + uuid.uuid4().hex,
        "origin_window_id": normalized,
        "origin_tab_id": _text(preferred_tab_id, 512),
        "window_id": normalized,
        "primary_surface_key": "",
        "alias_map": {},
        "surfaces": [],
    }


def capture(raw: Any, ws, *, session_id: str | None = None) -> dict | None:
    """Validate one renderer ref and capture its bounded DOM/ARIA preview."""
    if not isinstance(raw, dict):
        return None
    raw_window_id = (
        _text(raw.get("window_id"), 160)
        if raw.get("version") == 1 else ""
    )
    if raw_window_id and not _text(raw.get("tab_id"), 512):
        from openprogram.host.resources import desktop_link

        if not any(
            owner is ws and window_id == raw_window_id
            for owner, window_id, _revision
            in desktop_link.registered_desktop_windows()
        ):
            return None
        return window_context(raw_window_id)
    descriptor = _descriptor(raw)
    if descriptor is None:
        return None
    region = descriptor["region"]
    aliases = [region, "web:1"]
    if descriptor["focused"]:
        aliases.append("focused")
    surface = {
        "surface_key": "s1",
        "aliases": aliases,
        "kind": "web_tab",
        "window_id": descriptor["window_id"],
        "tab_id": descriptor["tab_id"],
        "region": region,
        "title": descriptor["title"],
        "origin": descriptor["origin"],
        "capabilities": [],
        "preview_status": "disabled",
        "untrusted_content": True,
    }
    context = {
        "context_id": "surface_ctx_" + uuid.uuid4().hex,
        "origin_window_id": descriptor["window_id"],
        "origin_tab_id": descriptor["tab_id"],
        "window_id": descriptor["window_id"],
        "primary_surface_key": "s1",
        "alias_map": {alias: "s1" for alias in aliases},
        "surfaces": [surface],
    }
    if descriptor["access"] != "enabled":
        return context

    from openprogram.host.resources import desktop_link
    connection_revision = desktop_link.ensure_connection_revision(ws)
    background = raw.get("background") is True
    preview_command = {
        "op": "preview",
        **({"session_id": session_id} if session_id else {}),
        "window_id": descriptor["window_id"],
        "tab_id": descriptor["tab_id"],
    }
    if descriptor["geometry_revision"]:
        preview_command["expected_geometry_revision"] = descriptor[
            "geometry_revision"
        ]
    if background:
        preview_command["background"] = True
    result = desktop_link.request_on_ws(ws, preview_command, timeout=5.0)
    target_id = result.get("target_id") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or not result.get("ok")
        or result.get("window_id") != descriptor["window_id"]
        or result.get("tab_id") != descriptor["tab_id"]
        or not isinstance(target_id, str)
        or not target_id
        or (
            descriptor["geometry_revision"]
            and _revision(result.get("geometry_revision"))
            != descriptor["geometry_revision"]
        )
    ):
        surface["preview_status"] = "unavailable"
        return context
    try:
        binding_id = desktop_link.register_binding(
            ws,
            descriptor["window_id"],
            descriptor["tab_id"],
            target_id,
            geometry_revision=descriptor["geometry_revision"],
            expected_connection_revision=connection_revision,
            allow_background=background,
        )
    except RuntimeError:
        surface["preview_status"] = "unavailable"
        return context
    revisions = desktop_link.binding_revisions(binding_id)
    surface.update({
        "title": _text(result.get("title"), 240) or surface["title"],
        "origin": _origin(str(result.get("url") or "")) or surface["origin"],
        "capabilities": ["observe", "interact", "navigate"],
        "preview_status": "ready",
        "preview": _preview(result.get("preview")),
        "binding_id": binding_id,
        "page_key": desktop_link.binding_page_key(binding_id),
        "connection_generation": _binding_generation(binding_id),
        **revisions,
    })
    try:
        _record_page_display(
            surface["page_key"],
            title=str(surface.get("title") or ""),
            target=str(result.get("url") or ""),
            connection_generation=surface["connection_generation"],
        )
    except Exception:
        # Display cache only; the surface context is returned regardless.
        _log.debug("page display not recorded for %s", surface.get("page_key"), exc_info=True)
    return context


def _record_page_display(page_key: str, **display) -> None:
    """Record a Page's title and target on its resource, when the browser is enabled."""
    from openprogram.programs._host import services as _services
    browser = _services.lookup("browser")
    if browser is not None:
        browser.pages.BrowserResourceStore().update_display(page_key, **display)


def tool_enabled(context: dict | None) -> bool:
    return bool(context and any(
        "observe" in (surface.get("capabilities") or [])
        and surface.get("binding_id")
        for surface in context.get("surfaces") or []
        if isinstance(surface, dict)
    ))


def browser_pages_available(context: dict | None) -> bool:
    """Whether this turn may use the registered OpenProgram Page inventory."""
    if context and any(
        isinstance(surface, dict)
        and surface.get("preview_status") == "disabled"
        for surface in context.get("surfaces") or []
    ):
        return False
    if tool_enabled(context):
        return True
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1" and (context or {}).get("origin_window_id"):
        return True  # The window's Pages are reached through the parent's bridge.
    try:
        from openprogram.host.resources import background_browser, desktop_link

        # With no App window there is still somewhere to open a page: the
        # browser the tab driver starts itself.
        return bool(desktop_link.registered_desktop_windows()) or background_browser.available()
    except Exception:
        return False




def bind(context: dict | None):
    return _current.set(context)


def reset(token) -> None:
    _current.reset(token)


def current() -> dict | None:
    return _current.get()


def resolve_binding(surface: str = "") -> str:
    context = current()
    if not tool_enabled(context):
        raise RuntimeError("no accessible surface is bound to this turn")
    key = (surface or "").strip() or context.get("primary_surface_key")
    key = (context.get("alias_map") or {}).get(key, key)
    for item in context.get("surfaces") or []:
        if item.get("surface_key") == key and item.get("binding_id"):
            return str(item["binding_id"])
    raise RuntimeError(f"surface {surface!r} is not available in this turn")


def resolve_page_key(surface: str = "") -> str:
    context = current()
    if not tool_enabled(context):
        raise RuntimeError("no accessible surface is bound to this turn")
    key = (surface or "").strip() or context.get("primary_surface_key")
    key = (context.get("alias_map") or {}).get(key, key)
    for item in context.get("surfaces") or []:
        if item.get("surface_key") == key and item.get("binding_id"):
            return str(item.get("page_key") or item["binding_id"])
    raise RuntimeError(f"surface {surface!r} is not available in this turn")


DESKTOP_UNAVAILABLE_ERROR = (
    "OpenProgram desktop app is not connected. "
    "Launch the desktop app to open a background web tab."
)


def _local_file_url(url: str) -> dict | None:
    """A file:// Page the agent may read, as a canonical URL; None for other schemes.

    Desktop tabs show local files like the address bar does; the agent opens
    one only where its sandbox lets it read the file directly.
    """
    from urllib.parse import unquote, urlsplit
    from pathlib import Path

    if not isinstance(url, str) or not url[:7].lower() == "file://":
        return None
    parts = urlsplit(url)
    if parts.netloc not in {"", "localhost"}:
        return {"ok": False, "reason_code": "unsupported_url",
                "error": "file:// URLs must name a local path (file:///...)."}
    path = os.path.realpath(unquote(parts.path))
    from openprogram.host.sandbox import validate_read_path
    violation = validate_read_path(path)
    if violation:
        from openprogram.runtime.permissions import refusal
        return {"ok": False, "reason_code": "unsupported_url",
                "error": refusal.sandbox("open_page", violation).text}
    if not os.path.exists(path):
        return {"ok": False, "reason_code": "unsupported_url",
                "error": f"local file not found: {path}"}
    return {"ok": True, "url": Path(path).as_uri() + (f"#{parts.fragment}" if parts.fragment else "")}


def open_page(
    url: str,
    *,
    window_id: str = "",
    background: bool = False,
) -> dict:
    """Open a desktop web tab and capture it as a Page context.

    Success carries the opened Page as ``surfaces``. Failure is a dict with
    ``ok=False`` and ``reason_code`` (never a silent empty context).
    """
    from openprogram.host.security.url_policy import URLPolicyError, normalize_url
    from openprogram.host.resources import desktop_link

    local = _local_file_url(url)
    if local is not None:
        if local.get("ok") is False:
            return local
        normalized = local["url"]
    else:
        try:
            normalized = normalize_url(url).normalized_url
        except URLPolicyError as exc:
            return {
                "ok": False,
                "reason_code": "unsupported_url",
                "error": f"unsupported url ({exc.reason}): {exc.safe_url}",
            }

    from openprogram.runtime.execution.run_control import get_current_session_id

    session_id = get_current_session_id()
    requested_window_id = _text(window_id, 160)
    turn = current() or {}
    origin_window_id = _text(turn.get("origin_window_id") or turn.get("window_id"), 160)
    if (
        background and not requested_window_id
        and os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1"
    ):
        # The parent opens a background Page only in the run's own window. A
        # scheduled run has that window and no Page in it yet, so the window
        # is named here; unnamed, the parent refuses every such open.
        requested_window_id = origin_window_id
    command = {
        "op": "open",
        "url": normalized,
        **({"session_id": session_id} if session_id else {}),
        **({"window_id": requested_window_id} if requested_window_id else {}),
        **({"background": True} if background else {}),
    }
    owner_ws = None
    connection_revision = 0
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        result = desktop_link.request(command, 15.0)
    else:
        registered = desktop_link.registered_desktop_windows()
        connections = desktop_link.connections()
        if requested_window_id:
            selected = next((
                entry for entry in registered
                if entry[1] == requested_window_id
            ), None)
        else:
            # The turn's own window while it is there; with none, the only window.
            selected = next((
                entry for entry in registered
                if origin_window_id and entry[1] == origin_window_id
            ), None) or (registered[0] if len(registered) == 1 else None)
        if selected is not None:
            owner_ws, selected_window_id, connection_revision = selected
            requested_window_id = selected_window_id
        elif not requested_window_id and len(connections) == 1:
            owner_ws = connections[0]
            connection_revision = desktop_link.ensure_connection_revision(owner_ws)
        else:
            return {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": DESKTOP_UNAVAILABLE_ERROR,
            }
        result = desktop_link.request_on_ws(owner_ws, command, timeout=15.0)

    result_value = result if isinstance(result, dict) else {}
    if (
        background
        and result_value.get("reason_code") == desktop_link.RESPONSE_TIMEOUT_REASON_CODE
    ):
        return page_cleanup_failure(str(
            result_value.get("error")
            or "desktop Page creation timed out before cleanup was confirmed"
        ))
    result_window_id = _text(result_value.get("window_id"), 160)
    tab_id = _text(result_value.get("tab_id"), 512)
    target_id = _text(result_value.get("target_id"), 512)
    agent_owned = (
        desktop_link.validated_open_ownership(result_value).get("created") is True
    )

    def rollback_opened_page() -> dict | None:
        if not agent_owned:
            return None
        rollback_window_id = requested_window_id or result_window_id
        if owner_ws is None or not rollback_window_id or not tab_id:
            return page_cleanup_failure(
                "the opened Page identity could not be safely closed"
            )
        close_result: dict = {}
        for _ in range(2):
            try:
                candidate = desktop_link.request_on_ws(
                    owner_ws,
                    {
                        "op": "close",
                        "window_id": rollback_window_id,
                        "tab_id": tab_id,
                    },
                    timeout=15.0,
                )
                close_result = (
                    candidate if isinstance(candidate, dict) else {
                        "ok": False,
                        "error": (
                            "desktop app returned an invalid Page close result"
                        ),
                    }
                )
            except Exception as exc:
                close_result = {
                    "ok": False,
                    "error": f"Page close failed ({type(exc).__name__}: {exc})",
                }
            if close_result.get("ok"):
                break
        if not isinstance(close_result, dict) or not close_result.get("ok"):
            return page_cleanup_failure(
                str((close_result or {}).get("error") or "Page close was rejected")
                if isinstance(close_result, dict) else
                "desktop app returned an invalid Page close result"
            )
        return None

    if not isinstance(result, dict) or not result.get("ok"):
        if result_value.get("reason_code") == "page_cleanup_failed":
            failure = page_cleanup_failure(str(
                result_value.get("error") or "Page close was rejected"
            ))
            for key in (
                "status", "success", "infeasible_declared",
                "handoff_instruction",
            ):
                if key in result_value:
                    failure[key] = result_value[key]
            return failure
        failure = {
            "ok": False,
            "reason_code": str(
                result_value.get("reason_code") or "desktop_unavailable"
            ),
            "error": (
                str(result_value.get("error"))
                if result_value.get("error") else
                "desktop app did not create a web tab (missing tab identity)"
            ),
        }
        for key in (
            "status", "success", "infeasible_declared", "handoff_instruction",
        ):
            if key in result_value:
                failure[key] = result_value[key]
        return failure
    if (
        not result_window_id
        or (requested_window_id and result_window_id != requested_window_id)
        or not tab_id
        or not target_id
    ):
        rollback_failure = rollback_opened_page()
        if rollback_failure is not None:
            return rollback_failure
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "desktop app returned another window or an incomplete Page",
        }
    window_id = result_window_id
    binding_id = _text(result.get("binding_id"), 160)
    if not binding_id:
        if owner_ws is None:
            return {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "desktop app did not bind the opened web tab",
            }
        try:
            binding_id = desktop_link.register_binding(
                owner_ws,
                window_id,
                tab_id,
                target_id,
                geometry_revision=_revision(result.get("geometry_revision")),
                expected_connection_revision=connection_revision,
                allow_background=True,
            )
        except Exception as exc:
            rollback_failure = rollback_opened_page()
            if rollback_failure is not None:
                return rollback_failure
            return {
                "ok": False,
                "reason_code": "page_context_stale",
                "error": f"opened Page could not be bound ({type(exc).__name__})",
            }
    revisions = desktop_link.binding_revisions(binding_id)
    aliases = ["web:1"]
    if not background:
        aliases.append("focused")
    surface = {
        "surface_key": "p1",
        "aliases": aliases,
        "kind": "web_tab",
        "window_id": window_id,
        "tab_id": tab_id,
        "target_id": target_id,
        "region": "background" if background else "center",
        "title": _text(result.get("title"), 240),
        "origin": _origin(str(result.get("url") or normalized)),
        "capabilities": ["observe", "interact", "navigate"],
        "preview_status": "ready",
        "visible": not background,
        "focused": not background,
        "agent_owned": agent_owned,
        "binding_id": binding_id,
        "page_key": (
            _text(result.get("page_key"), 160)
            or desktop_link.binding_page_key(binding_id)
        ),
        "connection_generation": _binding_generation(binding_id),
        **revisions,
    }
    alias_map = {"p1": "p1", "web:1": "p1"}
    if not background:
        alias_map["focused"] = "p1"
    try:
        _record_page_display(
            str(surface.get("page_key") or ""),
            title=str(surface.get("title") or ""),
            target=str(result.get("url") or normalized),
            connection_generation=surface["connection_generation"],
        )
    except Exception:
        # Display cache only; the opened page is returned regardless.
        _log.debug("page display not recorded for %s", surface.get("page_key"), exc_info=True)
    return {
        "context_id": "page_ctx_" + uuid.uuid4().hex,
        "window_id": window_id,
        "primary_surface_key": "p1",
        "alias_map": alias_map,
        "surfaces": [surface],
    }


def close_page(context: dict | None) -> dict:
    """Close one exact Page without selecting its tab or focusing its window."""
    from openprogram.host.resources import desktop_link

    value = context if isinstance(context, dict) else {}
    surface = next((
        item for item in value.get("surfaces") or []
        if isinstance(item, dict) and item.get("tab_id")
    ), {})
    if surface and surface.get("agent_owned") is not True:
        release_bindings(value)
        return {
            "ok": True,
            "closed": False,
            "released": True,
            "borrowed": True,
        }
    window_id = _text(surface.get("window_id") or value.get("window_id"), 160)
    tab_id = _text(surface.get("tab_id"), 512)
    if not window_id or not tab_id:
        return page_cleanup_failure(
            "Page context does not contain an exact window and tab"
        )
    command = {"op": "close", "window_id": window_id, "tab_id": tab_id}
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        result = desktop_link.request(command, 5.0)
    else:
        owner_ws = next((
            ws for ws, candidate, _revision
            in desktop_link.registered_desktop_windows()
            if candidate == window_id
        ), None)
        if owner_ws is None or owner_ws not in desktop_link.connections():
            return page_cleanup_failure(DESKTOP_UNAVAILABLE_ERROR)
        result = desktop_link.request_on_ws(owner_ws, command, timeout=5.0)
    if isinstance(result, dict) and result.get("ok"):
        release_bindings(value)
        return result
    error = (
        str(result.get("error") or "Page close was rejected")
        if isinstance(result, dict)
        else "desktop app returned an invalid Page close result"
    )
    return page_cleanup_failure(error)


def capture_pages(context: dict | None = None) -> dict:
    """Capture context-authorized Pages, or all registered Pages directly."""
    from openprogram.host.resources import desktop_link

    binding_id = next((
        str(item.get("binding_id"))
        for item in (context or {}).get("surfaces") or []
        if isinstance(item, dict) and item.get("binding_id")
    ), "")
    origin_window_id = _text(
        (context or {}).get("origin_window_id")
        or (context or {}).get("window_id"),
        160,
    )
    preferred_tab_id = _text(
        (context or {}).get("origin_tab_id")
        or next((
            item.get("tab_id")
            for item in (context or {}).get("surfaces") or []
            if isinstance(item, dict) and item.get("tab_id")
        ), ""),
        512,
    )
    if (
        context is not None
        and os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1"
    ):
        command = {
            "op": "capture_pages",
            **({"binding_id": binding_id} if binding_id else {}),
            **({"window_id": origin_window_id} if not binding_id else {}),
            **({"tab_id": preferred_tab_id} if preferred_tab_id else {}),
        }
        if not binding_id and not origin_window_id:
            raise RuntimeError("no accepted Page or origin window is available")
        bridged = desktop_link.request(command, 5.0)
        captured = bridged.get("context") if isinstance(bridged, dict) else None
        if not isinstance(bridged, dict) or not bridged.get("ok") or not isinstance(
            captured, dict
        ):
            error = (bridged or {}).get("error")
            message = str(error or "OpenProgram Page inventory is unavailable")
            if isinstance(bridged, dict) and bridged.get("reason_code") == "desktop_unavailable":
                raise DesktopUnavailableError(message)
            raise RuntimeError(message)
        return captured

    connected = desktop_link.connections()
    inventories: list[tuple[object, dict, int]] = []
    if context is not None:
        if binding_id:
            result = desktop_link.request_page_inventory(binding_id)
            owner = desktop_link.binding_owner_revision(binding_id)
            if owner is None:
                raise DesktopUnavailableError("accepted Page binding is unavailable")
            owner_ws, connection_revision = owner
            if (
                not isinstance(result, dict) or not result.get("ok")
                or not _text(result.get("window_id"), 160)
                or not isinstance(result.get("pages"), list)
            ):
                raise RuntimeError("OpenProgram Page inventory is unavailable")
            inventories.append((owner_ws, result, connection_revision))
        elif origin_window_id:
            selected = next((
                entry for entry in desktop_link.registered_desktop_windows()
                if entry[0] in connected and entry[1] == origin_window_id
            ), None)
            if selected is None:
                raise DesktopUnavailableError("originating Desktop window is unavailable")
            owner_ws, _window_id, connection_revision = selected
            result = desktop_link.request_on_ws(
                owner_ws,
                {"op": "list", "window_id": origin_window_id},
            )
            if (
                not isinstance(result, dict) or not result.get("ok")
                or result.get("window_id") != origin_window_id
                or not isinstance(result.get("pages"), list)
            ):
                raise RuntimeError("OpenProgram Page inventory is unavailable")
            inventories.append((owner_ws, result, connection_revision))
        else:
            raise RuntimeError("no accepted Page or origin window is available")
    else:
        registered = [
            (ws, window_id, revision)
            for ws, window_id, revision in desktop_link.registered_desktop_windows()
            if ws in connected
        ]
        if not registered:
            raise DesktopUnavailableError(
                "direct Page selection requires a registered Desktop window"
            )
        for owner_ws, expected_window_id, connection_revision in registered:
            command = {"op": "list"}
            if expected_window_id:
                command["window_id"] = expected_window_id
            result = desktop_link.request_on_ws(owner_ws, command)
            if (
                expected_window_id and result.get("ok")
                and result.get("window_id") != expected_window_id
            ):
                result = {"ok": False, "reason_code": "page_context_stale"}
            inventories.append((owner_ws, result, connection_revision))

    valid_inventories: list[tuple[object, dict, str, int]] = []
    seen_windows: set[str] = set()
    for owner_ws, result, connection_revision in inventories:
        window_id = _text(result.get("window_id"), 160) if isinstance(result, dict) else ""
        raw_pages = result.get("pages") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict) or not result.get("ok")
            or not window_id or not isinstance(raw_pages, list)
        ):
            continue
        if window_id in seen_windows:
            continue
        seen_windows.add(window_id)
        valid_inventories.append((
            owner_ws, result, window_id, connection_revision,
        ))
    if not valid_inventories:
        raise RuntimeError("OpenProgram Page inventory is unavailable")

    surfaces = []
    aliases: dict[str, str] = {}
    windows = []
    try:
        for owner_ws, result, window_id, connection_revision in valid_inventories:
            raw_pages = result["pages"]
            surface_by_tab: dict[str, str] = {}
            seen_tabs: set[str] = set()
            seen_targets: set[str] = set()
            window_pages = []
            for raw in raw_pages[:32]:
                if len(surfaces) >= 32:
                    break
                if not isinstance(raw, dict):
                    continue
                tab_id = _text(raw.get("tab_id"), 512)
                target_id = _text(raw.get("target_id"), 512)
                if (
                    not tab_id or not target_id
                    or tab_id in seen_tabs or target_id in seen_targets
                ):
                    continue
                seen_tabs.add(tab_id)
                seen_targets.add(target_id)
                visible = bool(raw.get("visible"))
                focused = bool(raw.get("focused"))
                region = raw.get("region")
                if not visible or region not in {"left", "right", "center"}:
                    region = "background"
                surface_key = f"p{len(surfaces) + 1}"
                page_aliases = [
                    surface_key,
                    f"web:{len(surfaces) + 1}",
                    f"window:{window_id}:tab:{tab_id}",
                    f"tab:{tab_id}",
                ]
                if focused:
                    page_aliases.extend([f"focused:{window_id}", "focused"])
                binding_id = desktop_link.register_binding(
                    owner_ws,
                    window_id,
                    tab_id,
                    target_id,
                    geometry_revision=_revision(raw.get("geometry_revision")),
                    allow_background=not visible,
                    expected_connection_revision=connection_revision,
                )
                surface = {
                    "surface_key": surface_key,
                    "aliases": page_aliases,
                    "kind": "web_tab",
                    "window_id": window_id,
                    "region": region,
                    "title": _text(raw.get("title"), 240),
                    "origin": _origin(str(raw.get("url") or "")),
                    "capabilities": ["observe", "interact", "navigate"],
                    "preview_status": "inventory",
                    "binding_id": binding_id,
                    "page_key": desktop_link.binding_page_key(binding_id),
                    "tab_id": tab_id,
                    "target_id": target_id,
                    "tab_entry_id": f"tab:{tab_id}",
                    "placement": {"mode": "single"},
                    "opener_tab_id": _text(raw.get("opener_tab_id"), 512),
                    "visible": visible,
                    "focused": focused,
                    **desktop_link.binding_revisions(binding_id),
                }
                surfaces.append(surface)
                window_pages.append(surface_key)
                surface_by_tab[tab_id] = surface_key
                for alias in page_aliases:
                    aliases.setdefault(alias, surface_key)

            if raw_pages and not window_pages and len(surfaces) < 32:
                raise RuntimeError("OpenProgram Page inventory has no valid Page")

            tab_entries = []
            surface_by_key = {
                item["surface_key"]: item
                for item in surfaces
                if item.get("window_id") == window_id
            }
            placed_pages: set[str] = set()
            for raw_entry in (result.get("tab_entries") or [])[:64]:
                if not isinstance(raw_entry, dict):
                    continue
                entry_id = _text(raw_entry.get("id"), 512)
                mode = raw_entry.get("mode")
                page_keys = [
                    surface_by_tab[tab_id]
                    for tab_id in raw_entry.get("tab_ids") or []
                    if tab_id in surface_by_tab
                ]
                if mode == "single":
                    page_keys = page_keys[:1]
                if not entry_id or mode not in {"single", "split"} or not page_keys:
                    continue
                entry = {"id": entry_id, "mode": mode, "pages": page_keys}
                raw_split = raw_entry.get("split")
                if mode == "split" and isinstance(raw_split, dict):
                    panes = []
                    for raw_pane in (raw_split.get("panes") or [])[:2]:
                        if not isinstance(raw_pane, dict):
                            continue
                        page_key = surface_by_tab.get(
                            str(raw_pane.get("tab_id") or "")
                        )
                        pane_id = _text(raw_pane.get("pane_id"), 512)
                        order = raw_pane.get("order")
                        if not page_key or not pane_id or type(order) is not int:
                            continue
                        panes.append({
                            "pane_id": pane_id, "order": order, "page": page_key,
                        })
                    entry["split"] = {
                        "axis": "horizontal",
                        "ratio": raw_split.get("ratio"),
                        "panes": panes,
                    }
                    if not panes:
                        continue
                    for pane in panes:
                        surface = surface_by_key[pane["page"]]
                        surface["tab_entry_id"] = entry_id
                        surface["placement"] = {
                            "mode": "split",
                            "pane_id": pane["pane_id"],
                            "order": pane["order"],
                        }
                        placed_pages.add(pane["page"])
                    for page_key in page_keys:
                        if page_key in placed_pages:
                            continue
                        surface_by_key[page_key]["tab_entry_id"] = entry_id
                        surface_by_key[page_key]["placement"] = {"mode": "split"}
                        placed_pages.add(page_key)
                else:
                    for page_key in page_keys:
                        surface_by_key[page_key]["tab_entry_id"] = entry_id
                        surface_by_key[page_key]["placement"] = {"mode": "single"}
                        placed_pages.add(page_key)
                tab_entries.append(entry)
            for page_key in window_pages:
                if page_key in placed_pages:
                    continue
                surface = surface_by_key[page_key]
                tab_entries.append({
                    "id": surface["tab_entry_id"],
                    "mode": "single",
                    "pages": [page_key],
                })

            focused_page = surface_by_tab.get(
                _text(result.get("focused_tab_id"), 512),
                next((
                    item["surface_key"]
                    for item in surface_by_key.values() if item.get("focused")
                ), ""),
            )
            window_primary = focused_page or next((
                item["surface_key"]
                for item in surface_by_key.values() if item.get("visible")
            ), window_pages[0] if window_pages else "")
            active_tab_entry_id = _text(result.get("active_tab_entry_id"), 512)
            if not any(entry["id"] == active_tab_entry_id for entry in tab_entries):
                active_tab_entry_id = next((
                    entry["id"] for entry in tab_entries
                    if window_primary in entry["pages"]
                ), "")
            windows.append({
                "window_id": window_id,
                "inventory_revision": _revision(result.get("inventory_revision")),
                "active_tab_entry_id": active_tab_entry_id,
                "focused_page": focused_page,
                "tab_entries": tab_entries,
                "pages": window_pages,
            })
    except Exception:
        for surface in surfaces:
            desktop_link.release_binding(str(surface["binding_id"]))
        raise
    preferred = next((
        item["surface_key"] for item in surfaces
        if preferred_tab_id and item.get("tab_id") == preferred_tab_id
    ), "")
    if preferred_tab_id and not preferred:
        for surface in surfaces:
            desktop_link.release_binding(str(surface["binding_id"]))
        raise RuntimeError("the submitted Page is unavailable")
    primary_window = next((
        window for window in windows if preferred in window["pages"]
    ), windows[0])
    primary = preferred or primary_window["focused_page"] or next((
        item["surface_key"]
        for item in surfaces
        if item.get("window_id") == primary_window["window_id"]
        and item.get("visible")
    ), surfaces[0]["surface_key"] if surfaces else "")
    return {
        "context_id": "page_ctx_" + uuid.uuid4().hex,
        "window_id": primary_window["window_id"],
        "inventory_revision": primary_window["inventory_revision"],
        "active_tab_entry_id": primary_window["active_tab_entry_id"],
        "focused_page": primary_window["focused_page"],
        "tab_entries": primary_window["tab_entries"],
        "windows": windows,
        "primary_surface_key": primary,
        "alias_map": aliases,
        "surfaces": surfaces,
    }


_ALL_WINDOWS = object()


def capture_pages_for_listing(context: Any = _ALL_WINDOWS, *, wait: bool = True) -> dict:
    """Capture Pages for ``list_pages`` without depending on one window.

    A disconnected originating window or binding is not an inventory failure.
    In the worker, the remaining registered windows stay discoverable. While
    no Desktop window is registered (the App is restarting), wait up to
    ``DESKTOP_RECONNECT_WAIT_SECONDS`` for one before raising
    ``DesktopUnavailableError``. A workflow subprocess stays confined to its
    originating window and only waits for that window. An online window whose
    inventory fails still raises at once and never promotes another window.
    ``wait=False`` is for a caller that knows the App is not running: there
    is no restart to wait out.
    """
    from openprogram.runtime.conversation.run_control import check_cancelled

    confined = os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1"
    deadline = time.monotonic() + (max(0.0, DESKTOP_RECONNECT_WAIT_SECONDS) if wait else 0.0)
    while True:
        try:
            if context is _ALL_WINDOWS:
                return capture_pages()
            return capture_pages(context)
        except DesktopUnavailableError:
            if context is not _ALL_WINDOWS and context is not None and not confined:
                try:
                    return capture_pages()
                except DesktopUnavailableError:
                    pass
            if time.monotonic() >= deadline:
                raise
        check_cancelled()
        time.sleep(_DESKTOP_RECONNECT_POLL_SECONDS)


def without_primary_page(context: dict) -> dict:
    """The same Pages, with none picked for a call that names no Page. A
    capture picks the one in front, which a detached turn must leave alone."""
    return {**context, "primary_surface_key": ""}


def release_bindings(context: dict | None) -> None:
    if not context:
        return
    from openprogram.host.resources import desktop_link
    for surface in context.get("surfaces") or []:
        binding_id = surface.get("binding_id") if isinstance(surface, dict) else None
        if binding_id:
            desktop_link.release_binding(str(binding_id))
