"""Session-scoped command contract for the built-in browser Page."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from contextlib import suppress
import copy
import threading
import uuid
from typing import Any, Callable, Mapping

from openprogram.programs import ToolReturn
from openprogram.web_use_contract import (
    SUPPORTED_WEB_USE_BACKENDS,
    normalize_web_use_arguments,
)


SUPPORTED_BACKENDS = SUPPORTED_WEB_USE_BACKENDS
DEFAULT_BACKEND = SUPPORTED_BACKENDS[0]


def _unresolved_session_id(value: str) -> bool:
    return str(value or "").strip().lower() in {"", "pending"}


def _session_frame_id(session: WebUseSession) -> str:
    frame_id = str(session.state.get("frame_id") or "").strip()
    if frame_id:
        return frame_id
    controller = session.controller
    frame = getattr(controller, "_frame", None) if controller is not None else None
    if isinstance(frame, dict):
        return str(frame.get("frame_id") or "").strip()
    return ""


def _result_frame_id(result: Any) -> str:
    payload = (
        result.json_data
        if isinstance(result, ToolReturn) and isinstance(result.json_data, dict)
        else result if isinstance(result, dict) else None
    )
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("frame_id") or "").strip()


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return False
    return isinstance(exc, PlaywrightTimeoutError)


@dataclass
class WebUseSession:
    id: str
    backend: str
    binding_id: str
    page_key: str = ""
    page_revision: int = 0
    access_revision: int = 0
    geometry_revision: int = 0
    owner_id: str = ""
    page_context: dict[str, Any] | None = None
    controller: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    operation_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False,
    )
    closing: bool = False
    closed: bool = False


class ControllerBackend:
    """Canonical command adapter over one exact BrowserPageController."""

    def __init__(self, name: str, controller_factory: Callable[[], Any]) -> None:
        self.name = name
        self._controller_factory = controller_factory

    def _controller(self, session: WebUseSession):
        if session.controller is None:
            controller = self._controller_factory()
            controller.binding_id = session.binding_id
            controller.page_revision = session.page_revision
            controller.access_revision = session.access_revision
            controller.geometry_revision = session.geometry_revision
            session.controller = controller
        return session.controller

    def observe(self, session: WebUseSession, arguments: Mapping[str, Any]):
        del arguments
        return self._controller(session).execute(action="observe")

    def act(self, session: WebUseSession, arguments: Mapping[str, Any]):
        return self._controller(session).execute(**dict(arguments))

    def verify(self, session: WebUseSession, arguments: Mapping[str, Any]):
        params = dict(arguments)
        params["action"] = "verify"
        return self._controller(session).execute(**params)

    def close(self, session: WebUseSession) -> None:
        if session.controller is not None:
            session.controller.close()


class WebUseSessionRegistry:
    def __init__(
        self,
        *,
        adapters: Mapping[str, Any] | None = None,
        controller_factory: Callable[[], Any] | None = None,
        release_context: Callable[[dict | None], None] | None = None,
        page_key_resolver: Callable[[str], str] | None = None,
        binding_revision_resolver: Callable[[str], Mapping[str, int]] | None = None,
        binding_validator: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        if adapters is None:
            if controller_factory is None:
                from . import _new_controller
                controller_factory = _new_controller
            from .mcp_backends import OfficialMCPPageBackend
            adapters = {
                "playwright_mcp": OfficialMCPPageBackend(
                    "playwright_mcp", controller_factory,
                ),
                "chrome_devtools_mcp": OfficialMCPPageBackend(
                    "chrome_devtools_mcp", controller_factory,
                ),
                "open_claude_chrome": ControllerBackend(
                    "open_claude_chrome", controller_factory,
                ),
            }
        missing = set(SUPPORTED_BACKENDS) - set(adapters)
        if missing:
            raise ValueError(f"missing computer use adapters: {sorted(missing)}")
        self._adapters = dict(adapters)
        self._sessions: dict[str, WebUseSession] = {}
        self._page_leases: dict[str, str] = {}
        self._page_capabilities: dict[str, dict[str, Any]] = {}
        self._closing_all = False
        self._closing_owners: set[str] = set()
        if release_context is None:
            from openprogram.agent.surface_context import release_bindings
            release_context = release_bindings
        if page_key_resolver is None:
            from openprogram.webui.ws_actions.webtab import binding_page_key
            page_key_resolver = binding_page_key
        if binding_revision_resolver is None:
            from openprogram.webui.ws_actions.webtab import binding_revisions
            binding_revision_resolver = binding_revisions
        self._binding_validator_accepts_revisions = binding_validator is None
        if binding_validator is None:
            from openprogram.webui.ws_actions.webtab import request_bound_tab
            binding_validator = request_bound_tab
        self._release_context = release_context
        self._page_key_resolver = page_key_resolver
        self._binding_revision_resolver = binding_revision_resolver
        self._binding_validator = binding_validator
        self._lock = threading.RLock()

    def _latest_owner_session_id(self, owner_id: str) -> str:
        if not owner_id:
            return ""
        with self._lock:
            for session in reversed(list(self._sessions.values())):
                if (
                    session.owner_id == owner_id
                    and not session.closing
                    and not session.closed
                ):
                    return session.id
        return ""

    def _detach_locked(self, session: WebUseSession) -> None:
        self._sessions.pop(session.id, None)
        for token, capability in list(self._page_capabilities.items()):
            if capability.get("context") is session.page_context:
                self._page_capabilities.pop(token, None)

    def _cleanup_session(
        self, session: WebUseSession, *, suppress_errors: bool,
    ) -> None:
        """Close one locked session before making its Page available again."""
        session.closing = True
        with self._lock:
            self._detach_locked(session)
        error: Exception | None = None
        try:
            self._adapters[session.backend].close(session)
        except Exception as exc:
            error = exc
        try:
            self._release_context(session.page_context)
        except Exception as exc:
            if error is None:
                error = exc
        finally:
            session.closed = True
            with self._lock:
                if self._page_leases.get(session.page_key) == session.id:
                    self._page_leases.pop(session.page_key, None)
        if error is not None and not suppress_errors:
            raise error

    def list_pages(self, *, context: dict, owner_id: str) -> dict[str, Any]:
        """Issue opaque, single-use capabilities for exact Page bindings."""
        pages = []
        with self._lock:
            if self._closing_all or owner_id in self._closing_owners:
                return {"ok": False, "reason_code": "owner_closing", "pages": []}
            for item in context.get("surfaces") or []:
                if not isinstance(item, dict) or not item.get("binding_id"):
                    continue
                token = "pct_" + uuid.uuid4().hex
                binding_id = str(item["binding_id"])
                surface_key = str(item.get("surface_key") or "")
                capability_context = {
                    "context_id": str(context.get("context_id") or ""),
                    "window_id": str(
                        item.get("window_id") or context.get("window_id") or ""
                    ),
                    "primary_surface_key": surface_key,
                    "alias_map": {
                        str(alias): surface_key
                        for alias in item.get("aliases") or []
                    },
                    "surfaces": [copy.deepcopy(item)],
                }
                self._page_capabilities[token] = {
                    "owner_id": owner_id,
                    "binding_id": binding_id,
                    "page_key": (
                        str(item.get("page_key") or "")
                        or self._page_key_resolver(binding_id)
                        or binding_id
                    ),
                    "context": capability_context,
                    "page_revision": int(item.get("page_revision") or 0),
                    "access_revision": int(item.get("access_revision") or 0),
                    "geometry_revision": int(item.get("geometry_revision") or 0),
                    "consumed": False,
                }
                pages.append({
                    "page": item.get("surface_key"),
                    "window_id": item.get("window_id") or context.get("window_id") or "",
                    "aliases": list(item.get("aliases") or []),
                    "region": item.get("region"),
                    "title": item.get("title") or "",
                    "origin": item.get("origin") or "",
                    "tab_id": item.get("tab_id") or "",
                    "tab_entry_id": item.get("tab_entry_id") or "",
                    "placement": copy.deepcopy(
                        item.get("placement") or {"mode": "single"}
                    ),
                    "opener_tab_id": item.get("opener_tab_id") or "",
                    "visible": bool(item.get("visible")),
                    "focused": bool(item.get("focused")),
                    "capabilities": list(item.get("capabilities") or []),
                    "page_context_token": token,
                })
        return {
            "ok": True,
            "browser_context_id": str(context.get("context_id") or ""),
            "window_id": str(context.get("window_id") or ""),
            "inventory_revision": int(context.get("inventory_revision") or 0),
            "active_tab_entry_id": str(
                context.get("active_tab_entry_id") or ""
            ),
            "focused_page": str(context.get("focused_page") or ""),
            "tab_entries": copy.deepcopy(context.get("tab_entries") or []),
            "windows": copy.deepcopy(context.get("windows") or []),
            "pages": pages,
        }

    def execute(
        self,
        *,
        command: str,
        backend: str = "",
        web_session_id: str = "",
        binding_id: str = "",
        page_key: str = "",
        owner_id: str = "",
        page_context_token: str = "",
        page_context: dict[str, Any] | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = dict(normalize_web_use_arguments({"arguments": arguments}).get("arguments") or {})
        if _unresolved_session_id(web_session_id):
            web_session_id = (
                "" if command == "observe"
                else self._latest_owner_session_id(owner_id)
            )
        created_session = command == "observe" and not web_session_id
        reused_session = False
        if created_session:
            revisions: Mapping[str, int] = {}
            selected = backend or DEFAULT_BACKEND
            if selected not in self._adapters:
                return {"ok": False, "reason_code": "unsupported_backend"}
            if page_context_token:
                with self._lock:
                    capability = self._page_capabilities.get(page_context_token)
                    if capability is None:
                        return {"ok": False, "reason_code": "page_context_not_found"}
                    if capability["owner_id"] != owner_id:
                        return {"ok": False, "reason_code": "page_context_owner_mismatch"}
                    if capability["consumed"]:
                        return {"ok": False, "reason_code": "page_context_consumed"}
                    binding_id = capability["binding_id"]
                    page_key = capability["page_key"]
                    page_context = capability["context"]
                    revisions = {
                        "page_revision": capability["page_revision"],
                        "access_revision": capability["access_revision"],
                        "geometry_revision": capability["geometry_revision"],
                    }
            if not binding_id:
                return {"ok": False, "reason_code": "page_context_required"}
            page_key = page_key or self._page_key_resolver(binding_id) or binding_id
            revisions = revisions or self._binding_revision_resolver(binding_id)
            if not revisions and page_context:
                revisions = next((
                    item for item in page_context.get("surfaces") or []
                    if isinstance(item, dict)
                    and item.get("binding_id") == binding_id
                ), {})
            session = WebUseSession(
                id="cs_" + uuid.uuid4().hex,
                backend=selected,
                binding_id=binding_id,
                page_key=page_key,
                page_revision=int(revisions.get("page_revision") or 0),
                access_revision=int(revisions.get("access_revision") or 0),
                geometry_revision=int(revisions.get("geometry_revision") or 0),
                owner_id=owner_id,
                page_context=page_context,
            )
            unused_capability_context = None
            with self._lock:
                if self._closing_all or owner_id in self._closing_owners:
                    return {"ok": False, "reason_code": "owner_closing"}
                leased_session_id = self._page_leases.get(page_key, "")
                leased_session = self._sessions.get(leased_session_id)
                if leased_session_id:
                    if leased_session is None:
                        return {"ok": False, "reason_code": "page_in_use"}
                    if (
                        leased_session.owner_id != owner_id
                        or leased_session.backend != selected
                        or leased_session.closing
                        or leased_session.closed
                    ):
                        return {"ok": False, "reason_code": "page_in_use"}
                    session = leased_session
                    created_session = False
                    reused_session = True
                if page_context_token:
                    capability = self._page_capabilities.get(page_context_token)
                    if capability is None:
                        return {"ok": False, "reason_code": "page_context_not_found"}
                    if capability["owner_id"] != owner_id:
                        return {"ok": False, "reason_code": "page_context_owner_mismatch"}
                    if capability["consumed"]:
                        return {"ok": False, "reason_code": "page_context_consumed"}
                    if reused_session:
                        unused_capability_context = capability["context"]
                        self._page_capabilities.pop(page_context_token, None)
                    else:
                        capability["consumed"] = True
                if not reused_session:
                    self._sessions[session.id] = session
                    self._page_leases[page_key] = session.id
            if unused_capability_context is not None:
                self._release_context(unused_capability_context)
        else:
            with self._lock:
                session = self._sessions.get(web_session_id)
            if session is None:
                return {"ok": False, "reason_code": "web_session_not_found"}

        with session.operation_lock:
            with self._lock:
                owner_closing = (
                    self._closing_all or session.owner_id in self._closing_owners
                )
            if session.closing or session.closed or owner_closing:
                return {"ok": False, "reason_code": "web_session_not_found"}
            if session.owner_id and owner_id != session.owner_id:
                return {"ok": False, "reason_code": "web_session_owner_mismatch"}
            if backend and backend != session.backend:
                return {
                    "ok": False,
                    "reason_code": "backend_mismatch",
                    "web_session_id": session.id,
                    "backend": session.backend,
                }

            if command in {"act", "verify"}:
                if not str(params.get("expected_frame_id") or "").strip():
                    frame_id = _session_frame_id(session)
                    if frame_id:
                        params["expected_frame_id"] = frame_id
            if command == "act":
                missing = [
                    name for name in ("action", "expected_frame_id")
                    if not isinstance(params.get(name), str)
                    or not params[name].strip()
                ]
                if missing:
                    return {
                        "ok": False,
                        "reason_code": "invalid_arguments",
                        "missing_arguments": missing,
                        "web_session_id": session.id,
                        "backend": session.backend,
                    }

            if command in {"observe", "act", "verify"}:
                revisions = self._binding_revision_resolver(session.binding_id)
                revision_changed = bool(revisions) and ((
                    session.page_revision
                    and int(revisions.get("page_revision") or 0)
                    != session.page_revision
                ) or (
                    session.access_revision
                    and int(revisions.get("access_revision") or 0)
                    != session.access_revision
                ) or (
                    session.geometry_revision
                    and int(revisions.get("geometry_revision") or 0)
                    != session.geometry_revision
                ))
                try:
                    validation = (
                        {"ok": False, "reason_code": "page_context_stale"}
                        if revision_changed
                        else (
                            self._binding_validator(
                                session.binding_id,
                                expected_page_revision=session.page_revision,
                                expected_access_revision=session.access_revision,
                                expected_geometry_revision=session.geometry_revision,
                            )
                            if self._binding_validator_accepts_revisions
                            else self._binding_validator(session.binding_id)
                        )
                    )
                except Exception:
                    validation = {"ok": False, "reason_code": "target_lost"}
                if not validation.get("ok"):
                    reason_code = str(
                        validation.get("reason_code") or "page_context_stale"
                    )
                    self._cleanup_session(session, suppress_errors=True)
                    return {
                        "ok": False,
                        "reason_code": reason_code,
                        "web_session_id": session.id,
                        "backend": session.backend,
                    }

            adapter = self._adapters[session.backend]
            try:
                if command == "observe":
                    result = adapter.observe(session, params)
                elif command == "act":
                    result = adapter.act(session, params)
                elif command == "verify":
                    result = adapter.verify(session, params)
                elif command == "close":
                    self._cleanup_session(session, suppress_errors=False)
                    result = {"ok": True, "closed": True}
                else:
                    return {"ok": False, "reason_code": "invalid_command"}
            except Exception as exc:
                if command == "observe" and not session.closing:
                    self._cleanup_session(session, suppress_errors=True)
                    raise
                if command != "act":
                    raise
                controller = session.controller
                invalidate = getattr(controller, "invalidate_external_frame", None)
                if callable(invalidate):
                    with suppress(Exception):
                        invalidate()
                result = {
                    "ok": False,
                    "reason_code": (
                        "timeout" if _is_timeout_error(exc)
                        else "backend_action_failed"
                    ),
                    "observe_required": True,
                }

            frame_id = _result_frame_id(result)
            if frame_id:
                session.state["frame_id"] = frame_id
            if created_session and not frame_id:
                self._cleanup_session(session, suppress_errors=True)

        if isinstance(result, ToolReturn):
            metadata = (
                dict(result.json_data) if isinstance(result.json_data, dict) else {}
            )
            metadata.setdefault("web_session_id", session.id)
            metadata.setdefault("backend", session.backend)
            if reused_session:
                metadata["session_reused"] = True
            result.json_data = metadata
            return result
        payload = dict(result) if isinstance(result, dict) else {"result": result}
        payload.setdefault("web_session_id", session.id)
        payload.setdefault("backend", session.backend)
        if reused_session:
            payload["session_reused"] = True
        return payload

    def close_all(self) -> None:
        with self._lock:
            if self._closing_all:
                return
            self._closing_all = True
            sessions = list(self._sessions.values())
            capabilities = [
                value for value in self._page_capabilities.values()
                if not value["consumed"]
            ]
            self._page_capabilities.clear()
        released = set()
        try:
            for session in sessions:
                with session.operation_lock:
                    if not session.closed:
                        self._cleanup_session(session, suppress_errors=True)
                    released.add(id(session.page_context))
            for capability in capabilities:
                context = capability["context"]
                key = id(context)
                if key not in released:
                    with suppress(Exception):
                        self._release_context(context)
                    released.add(key)
        finally:
            with self._lock:
                self._closing_all = False

    def release_page_capabilities(
        self, tokens: list[str], *, owner_id: str,
    ) -> int:
        """Release only unconsumed capabilities issued to one owner."""
        capabilities = []
        with self._lock:
            for token in dict.fromkeys(tokens):
                value = self._page_capabilities.get(token)
                if (
                    value is None
                    or value["owner_id"] != owner_id
                    or value["consumed"]
                ):
                    continue
                self._page_capabilities.pop(token, None)
                capabilities.append(value)
        released = set()
        for capability in capabilities:
            context = capability["context"]
            key = id(context)
            if key in released:
                continue
            with suppress(Exception):
                self._release_context(context)
            released.add(key)
        return len(capabilities)

    def revoke_screenshot(self, web_session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(web_session_id)
        if session is None:
            return
        with session.operation_lock:
            if session.closing or session.closed or session.controller is None:
                return
            revoke = getattr(session.controller, "revoke_screenshot", None)
            if callable(revoke):
                revoke()

    def release_owner(self, owner_id: str) -> None:
        """Release every session and unconsumed Page capability for one caller."""
        with self._lock:
            if owner_id in self._closing_owners:
                return
            self._closing_owners.add(owner_id)
            sessions = [s for s in self._sessions.values() if s.owner_id == owner_id]
            capabilities = []
            for token, value in list(self._page_capabilities.items()):
                if value["owner_id"] != owner_id:
                    continue
                self._page_capabilities.pop(token, None)
                if not value["consumed"]:
                    capabilities.append(value)
        released = set()
        try:
            for session in sessions:
                with session.operation_lock:
                    if not session.closed:
                        self._cleanup_session(session, suppress_errors=True)
                    released.add(id(session.page_context))
            for capability in capabilities:
                context = capability["context"]
                key = id(context)
                if key not in released:
                    with suppress(Exception):
                        self._release_context(context)
                    released.add(key)
        finally:
            with self._lock:
                self._closing_owners.discard(owner_id)


_registry: WebUseSessionRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> WebUseSessionRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = WebUseSessionRegistry()
        return _registry


def release_owner_if_initialized(owner_id: str) -> None:
    """Release a turn owner without creating Web Use state for ordinary turns."""
    with _registry_lock:
        registry = _registry
    if registry is not None:
        registry.release_owner(owner_id)


__all__ = [
    "WebUseSessionRegistry",
    "DEFAULT_BACKEND",
    "SUPPORTED_BACKENDS",
    "get_registry",
    "release_owner_if_initialized",
]
