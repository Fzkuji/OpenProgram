"""Session-scoped command contract for the built-in browser Page."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from contextlib import suppress
import threading
import uuid
from typing import Any, Callable, Mapping

from openprogram.programs import ToolReturn


SUPPORTED_BACKENDS = (
    "playwright_mcp",
    "chrome_devtools_mcp",
    "open_claude_chrome",
)
DEFAULT_BACKEND = SUPPORTED_BACKENDS[0]


@dataclass
class ComputerUseSession:
    id: str
    backend: str
    binding_id: str
    owner_id: str = ""
    page_context: dict[str, Any] | None = None
    controller: Any = None
    state: dict[str, Any] = field(default_factory=dict)


class ControllerBackend:
    """Canonical command adapter over one exact BrowserPageController."""

    def __init__(self, name: str, controller_factory: Callable[[], Any]) -> None:
        self.name = name
        self._controller_factory = controller_factory

    def _controller(self, session: ComputerUseSession):
        if session.controller is None:
            controller = self._controller_factory()
            controller.binding_id = session.binding_id
            session.controller = controller
        return session.controller

    def observe(self, session: ComputerUseSession, arguments: Mapping[str, Any]):
        del arguments
        return self._controller(session).execute(action="observe")

    def act(self, session: ComputerUseSession, arguments: Mapping[str, Any]):
        return self._controller(session).execute(**dict(arguments))

    def verify(self, session: ComputerUseSession, arguments: Mapping[str, Any]):
        params = dict(arguments)
        params["action"] = "verify"
        return self._controller(session).execute(**params)

    def close(self, session: ComputerUseSession) -> None:
        if session.controller is not None:
            session.controller.close()


class ComputerUseSessionRegistry:
    def __init__(
        self,
        *,
        adapters: Mapping[str, Any] | None = None,
        controller_factory: Callable[[], Any] | None = None,
        release_context: Callable[[dict | None], None] | None = None,
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
        self._sessions: dict[str, ComputerUseSession] = {}
        self._page_capabilities: dict[str, dict[str, Any]] = {}
        if release_context is None:
            from openprogram.agent.surface_context import release_bindings
            release_context = release_bindings
        self._release_context = release_context
        self._lock = threading.RLock()

    def list_pages(self, *, context: dict, owner_id: str) -> dict[str, Any]:
        """Issue opaque, single-use capabilities for exact Page bindings."""
        pages = []
        with self._lock:
            for item in context.get("surfaces") or []:
                if not isinstance(item, dict) or not item.get("binding_id"):
                    continue
                token = "pct_" + uuid.uuid4().hex
                self._page_capabilities[token] = {
                    "owner_id": owner_id,
                    "binding_id": str(item["binding_id"]),
                    "context": context,
                    "consumed": False,
                }
                pages.append({
                    "page": item.get("surface_key"),
                    "aliases": list(item.get("aliases") or []),
                    "region": item.get("region"),
                    "title": item.get("title") or "",
                    "origin": item.get("origin") or "",
                    "capabilities": list(item.get("capabilities") or []),
                    "page_context_token": token,
                })
        return {"ok": True, "pages": pages}

    def execute(
        self,
        *,
        command: str,
        backend: str = "",
        computer_session_id: str = "",
        binding_id: str = "",
        owner_id: str = "",
        page_context_token: str = "",
        page_context: dict[str, Any] | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = dict(arguments or {})
        created_session = command == "observe" and not computer_session_id
        if created_session:
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
                    capability["consumed"] = True
                    binding_id = capability["binding_id"]
                    page_context = capability["context"]
            if not binding_id:
                return {"ok": False, "reason_code": "page_context_required"}
            session = ComputerUseSession(
                id="cs_" + uuid.uuid4().hex,
                backend=selected,
                binding_id=binding_id,
                owner_id=owner_id,
                page_context=page_context,
            )
            with self._lock:
                self._sessions[session.id] = session
        else:
            with self._lock:
                session = self._sessions.get(computer_session_id)
            if session is None:
                return {"ok": False, "reason_code": "computer_session_not_found"}

        if session.owner_id and owner_id != session.owner_id:
            return {"ok": False, "reason_code": "computer_session_owner_mismatch"}

        if backend and backend != session.backend:
            return {
                "ok": False,
                "reason_code": "backend_mismatch",
                "computer_session_id": session.id,
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
                adapter.close(session)
                result = {"ok": True, "closed": True}
            else:
                return {"ok": False, "reason_code": "invalid_command"}
        except Exception:
            if command == "observe":
                with self._lock:
                    self._sessions.pop(session.id, None)
                try:
                    adapter.close(session)
                finally:
                    self._release_context(session.page_context)
            raise

        if created_session:
            frame_id = (
                result.json_data.get("frame_id")
                if isinstance(result, ToolReturn)
                and isinstance(result.json_data, dict)
                else result.get("frame_id") if isinstance(result, dict) else None
            )
            if not frame_id:
                with self._lock:
                    self._sessions.pop(session.id, None)
                    for token, capability in list(self._page_capabilities.items()):
                        if capability.get("context") is session.page_context:
                            self._page_capabilities.pop(token, None)
                with suppress(Exception):
                    adapter.close(session)
                self._release_context(session.page_context)

        if command == "close":
            with self._lock:
                self._sessions.pop(session.id, None)
                for token, capability in list(self._page_capabilities.items()):
                    if capability.get("context") is session.page_context:
                        self._page_capabilities.pop(token, None)
            self._release_context(session.page_context)

        if isinstance(result, ToolReturn):
            metadata = (
                dict(result.json_data) if isinstance(result.json_data, dict) else {}
            )
            metadata.setdefault("computer_session_id", session.id)
            metadata.setdefault("backend", session.backend)
            result.json_data = metadata
            return result
        payload = dict(result) if isinstance(result, dict) else {"result": result}
        payload.setdefault("computer_session_id", session.id)
        payload.setdefault("backend", session.backend)
        return payload

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._adapters[session.backend].close(session)
            self._release_context(session.page_context)

    def revoke_screenshot(self, computer_session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(computer_session_id)
        if session is None or session.controller is None:
            return
        revoke = getattr(session.controller, "revoke_screenshot", None)
        if callable(revoke):
            revoke()

    def release_owner(self, owner_id: str) -> None:
        """Release every session and unconsumed Page capability for one caller."""
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.owner_id == owner_id]
            for session in sessions:
                self._sessions.pop(session.id, None)
            capabilities = []
            for token, value in list(self._page_capabilities.items()):
                if value["owner_id"] != owner_id:
                    continue
                self._page_capabilities.pop(token, None)
                if not value["consumed"]:
                    capabilities.append(value)
        released = set()
        for session in sessions:
            self._adapters[session.backend].close(session)
            context = session.page_context
            key = id(context)
            if key not in released:
                self._release_context(context)
                released.add(key)
        for capability in capabilities:
            context = capability["context"]
            key = id(context)
            if key not in released:
                self._release_context(context)
                released.add(key)


_registry: ComputerUseSessionRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ComputerUseSessionRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ComputerUseSessionRegistry()
        return _registry


__all__ = [
    "ComputerUseSessionRegistry",
    "DEFAULT_BACKEND",
    "SUPPORTED_BACKENDS",
    "get_registry",
]
