"""Session-scoped command contract for the built-in browser Page."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
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
        self._lock = threading.RLock()

    def execute(
        self,
        *,
        command: str,
        backend: str = "",
        computer_session_id: str = "",
        binding_id: str = "",
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = dict(arguments or {})
        if command == "observe" and not computer_session_id:
            selected = backend or DEFAULT_BACKEND
            if selected not in self._adapters:
                return {"ok": False, "reason_code": "unsupported_backend"}
            session = ComputerUseSession(
                id="cs_" + uuid.uuid4().hex,
                backend=selected,
                binding_id=binding_id,
            )
            with self._lock:
                self._sessions[session.id] = session
        else:
            with self._lock:
                session = self._sessions.get(computer_session_id)
            if session is None:
                return {"ok": False, "reason_code": "computer_session_not_found"}

        if backend and backend != session.backend:
            return {
                "ok": False,
                "reason_code": "backend_mismatch",
                "computer_session_id": session.id,
                "backend": session.backend,
            }

        adapter = self._adapters[session.backend]
        if command == "observe":
            result = adapter.observe(session, params)
        elif command == "act":
            result = adapter.act(session, params)
        elif command == "verify":
            result = adapter.verify(session, params)
        elif command == "close":
            adapter.close(session)
            with self._lock:
                self._sessions.pop(session.id, None)
            result = {"ok": True, "closed": True}
        else:
            return {"ok": False, "reason_code": "invalid_command"}

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
