from __future__ import annotations

import asyncio
import inspect
import json
import math
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

import mcp.types as mcp_types
import anyio
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import McpError

from openprogram.agent.authority import (
    decide_capability,
    decide_tool_authority,
    mcp_web_control_authority,
    mcp_client_authority,
)
from openprogram.agent.session_db import SessionDB, default_db
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.execution.control import ObservedCancelSubmission, submit_observed_cancel
from openprogram.mcp.server.tools import json_result, prompt_result, to_mcp_content
from openprogram.providers.types import TextContent


@dataclass(frozen=True)
class MCPClientContext:
    client_id: str
    authority: Mapping[str, Any]

    def __post_init__(self) -> None:
        expected = mcp_client_authority(self.client_id)
        if dict(self.authority) != expected:
            raise ValueError("invalid MCP client authority")
        object.__setattr__(self, "authority", MappingProxyType(expected))


@dataclass
class ActiveMCPRequest:
    request_id: str
    session_id: str
    client_id: str
    thread_cancel: threading.Event
    tool_cancel: asyncio.Event
    execution_id: str = ""
    execution_status_version: int | None = None
    question_registry: Any | None = None
    cancel_requested: bool = False
    cancel_reason: str = ""
    cancel_command_id: str = ""
    activation_started: bool = False
    worker_done: threading.Event = field(default_factory=threading.Event)
    outer_abandoned: threading.Event = field(default_factory=threading.Event)


class _RegisterCancelEvent(Protocol):
    def __call__(
        self,
        session_id: str,
        event: threading.Event,
        *,
        execution_id: str,
    ) -> bool: ...


class _UnregisterCancelEvent(Protocol):
    def __call__(
        self,
        session_id: str,
        event: threading.Event,
        *,
        execution_id: str,
    ) -> None: ...


class _CurrentCancelEvent(Protocol):
    def __call__(
        self,
        session_id: str,
        *,
        execution_id: str,
    ) -> threading.Event | None: ...


def _default_config() -> Mapping[str, Any]:
    from openprogram.setup import _read_config

    return _read_config()


def _default_registry_get(name: str) -> AgentTool | None:
    from openprogram.programs._runtime import get

    return get(name)


def _default_registry_exposed_names() -> set[str]:
    from openprogram.programs._runtime import exposed_names

    return exposed_names()


def _default_register_cancel_event(
    session_id, event, *, execution_id: str,
) -> bool:
    del session_id, event, execution_id
    return True


def _default_unregister_cancel_event(
    session_id, event, *, execution_id: str,
) -> None:
    del session_id, event, execution_id


def _default_current_cancel_event(session_id, *, execution_id: str):
    del session_id, execution_id
    return None


def _default_acquire_cancel_cleanup(session_id, event) -> bool:
    del session_id, event
    return True


def _default_release_cancel_cleanup(session_id, event) -> None:
    del session_id, event


async def _default_cancel_execution(
    execution_id: str,
    *,
    command_id: str,
    expected_version: int,
    actor: Mapping[str, Any],
    reason_code: str,
) -> ObservedCancelSubmission:
    """Submit an MCP intent directly to the canonical control service."""
    from openprogram.execution import default_control_service

    return await submit_observed_cancel(
        default_control_service(),
        command_id=command_id,
        execution_id=execution_id,
        expected_version=expected_version,
        actor=actor,
        reason_code=reason_code,
    )


def _default_question_registry():
    from openprogram.agent.questions import get_question_registry

    return get_question_registry()


def _default_event_bus():
    from openprogram.events import get_event_bus

    return get_event_bus()


def _default_web_use_dispatch(arguments, *, owner_id):
    """Execute browser control inside the running worker process.

    The stdio MCP server is a separate process.  Page bindings and the
    renderer WebSocket registry live in the worker, so importing the browser
    tool here would create an empty, unrelated registry.
    """
    payload = _worker_web_use_request(
        "/api/web-use",
        {"arguments": dict(arguments), "owner_id": owner_id},
        timeout=120,
    )
    from openprogram.agent.types import AgentToolResult

    return AgentToolResult.model_validate(payload["result"])


def _default_web_use_release_owner(owner_id: str) -> None:
    _worker_web_use_request(
        "/api/web-use/release-owner",
        {"owner_id": owner_id},
        timeout=5,
    )


def _default_web_use_release_pages(owner_id: str, tokens: list[str]) -> None:
    _worker_web_use_request(
        "/api/web-use/release-pages",
        {"owner_id": owner_id, "page_context_tokens": list(tokens)},
        timeout=5,
    )


def _worker_web_use_request(
    path: str, body: Mapping[str, Any], *, timeout: float,
) -> dict[str, Any]:
    from openprogram.backend_endpoint import resolve_backend_endpoint

    endpoint = resolve_backend_endpoint()
    request = urllib.request.Request(
        endpoint.base_url + path,
        data=json.dumps(dict(body)).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": endpoint.authorization_header,
            "Content-Type": "application/json",
            "Host": endpoint.host,
            "X-Forwarded-Proto": endpoint.scheme,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        raise RuntimeError("web_use_worker_unavailable") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("web_use_worker_failed")
    return payload


def _best_effort(
    callback: Callable[..., Any], *args: Any, **kwargs: Any,
) -> Any:
    try:
        return callback(*args, **kwargs)
    except Exception:
        return None


_APPROVAL_GATE_DENIAL_TEXT = {
    "HARD_CONSTRAINT_DENIED": "[denied] hard constraint",
    "PERMISSION_RULE_DENY": "[denied] blocked by permission rule",
    "APPROVAL_UNAVAILABLE_NON_INTERACTIVE": (
        "[denied] approval unavailable for non-interactive MCP"
    ),
}


def _trusted_approval_denial(
    result: AgentToolResult,
    *,
    request: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> AgentToolResult | None:
    details = result.details
    if not isinstance(details, dict):
        return None
    reason_code = details.get("reason_code")
    if reason_code == "AUTHORITY_CAPABILITY_DENIED":
        decision = decide_tool_authority(request, tool_name, arguments)
        if decision.allowed or decision.reason_code != reason_code:
            return None
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        f"[denied] authority tier does not allow {decision.capability}"
                    )
                )
            ],
            details={
                "denied": True,
                "reason_code": reason_code,
                "capability": decision.capability,
            },
            is_error=True,
        )
    text = _APPROVAL_GATE_DENIAL_TEXT.get(reason_code)
    if text is None:
        return None
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"denied": True, "reason_code": reason_code},
        is_error=True,
    )


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _session_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError
    session_id = row.get("id")
    title = row.get("title")
    updated_at = row.get("updated_at")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(title, str)
        or not _number(updated_at)
    ):
        raise ValueError
    return {"id": session_id, "title": title, "updated_at": updated_at}


def _message_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError
    message_id = row.get("id")
    role = row.get("role")
    content = row.get("content")
    timestamp = row.get("timestamp")
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(role, str)
        or not role
        or not isinstance(content, str)
        or not _number(timestamp)
    ):
        raise ValueError
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "timestamp": timestamp,
    }


def _mcp_error(code: int, message: str) -> McpError:
    return McpError(mcp_types.ErrorData(code=code, message=message))


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        copied = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError
            copied[key] = _copy_json(item)
        return copied
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError


def _execution_error() -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text="Runtime tool execution failed")],
        details={"reason_code": "RUNTIME_TOOL_EXECUTION_FAILED"},
        is_error=True,
    )


def _web_session_id(raw: Any, arguments: Mapping[str, Any]) -> str:
    existing = arguments.get("web_session_id")
    if isinstance(existing, str) and existing:
        return existing
    payload: Any = raw
    if isinstance(raw, AgentToolResult):
        details = raw.details
        if isinstance(details, Mapping) and isinstance(details.get("json"), Mapping):
            payload = details["json"]
        else:
            payload = None
            for item in raw.content:
                text = getattr(item, "text", None)
                if not isinstance(text, str):
                    continue
                try:
                    candidate = json.loads(text)
                except (TypeError, ValueError):
                    continue
                if isinstance(candidate, Mapping):
                    payload = candidate
                    break
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get("web_session_id")
    return value if isinstance(value, str) else ""


def _web_page_context_tokens(raw: Any) -> list[str]:
    payload: Any = raw
    if isinstance(raw, AgentToolResult):
        details = raw.details
        if isinstance(details, Mapping) and isinstance(details.get("json"), Mapping):
            payload = details["json"]
        else:
            payload = None
            for item in raw.content:
                text = getattr(item, "text", None)
                if not isinstance(text, str):
                    continue
                try:
                    candidate = json.loads(text)
                except (TypeError, ValueError):
                    continue
                if isinstance(candidate, Mapping):
                    payload = candidate
                    break
    if not isinstance(payload, Mapping):
        return []
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return []
    return list(dict.fromkeys(
        token
        for page in pages
        if isinstance(page, Mapping)
        for token in [page.get("page_context_token")]
        if isinstance(token, str) and token
    ))


class MCPService:
    _process_session_lock = threading.RLock()
    _process_session_owners: dict[str, ActiveMCPRequest] = {}

    def __init__(
        self,
        context: MCPClientContext,
        *,
        session_db: SessionDB | None = None,
        config_getter: Callable[[], Mapping[str, Any]] | None = None,
        registry_get: Callable[[str], AgentTool | None] | None = None,
        registry_exposed_names: Callable[[], set[str]] | None = None,
        process_user_turn: Callable[..., Any] | None = None,
        register_cancel_event: _RegisterCancelEvent | None = None,
        unregister_cancel_event: _UnregisterCancelEvent | None = None,
        current_cancel_event: _CurrentCancelEvent | None = None,
        acquire_cancel_cleanup: Callable[[str, threading.Event], bool] | None = None,
        release_cancel_cleanup: Callable[[str, threading.Event], None] | None = None,
        cancel_execution: Callable[[str], Any] | None = None,
        question_registry_getter: Callable[[], Any] | None = None,
        event_bus_getter: Callable[[], Any] | None = None,
        web_use_dispatch: Callable[..., Any] | None = None,
        web_use_release_owner: Callable[[str], Any] | None = None,
        web_use_release_pages: Callable[[str, list[str]], Any] | None = None,
    ) -> None:
        self.context = context
        self._session_db = session_db or default_db()
        self._config_getter = config_getter or _default_config
        self._registry_get = registry_get or _default_registry_get
        self._registry_exposed_names = (
            registry_exposed_names or _default_registry_exposed_names
        )
        if process_user_turn is None:
            from openprogram.agent.dispatcher import process_user_turn as _canonical_turn_runner
            process_user_turn = _canonical_turn_runner
        self._process_user_turn = process_user_turn
        self._register_cancel_event = (
            register_cancel_event or _default_register_cancel_event
        )
        self._unregister_cancel_event = (
            unregister_cancel_event or _default_unregister_cancel_event
        )
        self._current_cancel_event = (
            current_cancel_event or _default_current_cancel_event
        )
        self._acquire_cancel_cleanup = (
            acquire_cancel_cleanup or _default_acquire_cancel_cleanup
        )
        self._release_cancel_cleanup = (
            release_cancel_cleanup or _default_release_cancel_cleanup
        )
        self._cancel_execution = cancel_execution or _default_cancel_execution
        self._question_registry_getter = (
            question_registry_getter or _default_question_registry
        )
        self._event_bus = (event_bus_getter or _default_event_bus)()
        self._control_connection_id = uuid.uuid4().hex
        self._web_use_owner_id = (
            f"mcp:{self.context.client_id}:{self._control_connection_id}"
        )
        self._web_use_dispatch = (
            web_use_dispatch or _default_web_use_dispatch
        )
        self._web_use_release_owner = (
            web_use_release_owner or _default_web_use_release_owner
        )
        self._web_use_release_pages = (
            web_use_release_pages or _default_web_use_release_pages
        )
        self._active_lock = threading.RLock()
        self._active_by_request: dict[str, ActiveMCPRequest] = {}
        self._request_by_session: dict[str, str] = {}
        self._registered_requests: dict[str, ActiveMCPRequest] = {}
        self._cleaning_sessions: set[str] = set()
        self._closed = False
        self._unsubscribe_questions = self._event_bus.subscribe(
            self._on_question_asked, types={"question.asked"}
        )

    def _on_question_asked(self, event: Any) -> None:
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            return
        question_id = payload.get("id")
        session_id = payload.get("session_id")
        payload_execution_id = payload.get("execution_id")
        if (
            type(question_id) is not str
            or not question_id
            or type(session_id) is not str
            or not session_id
        ):
            return
        with self._active_lock:
            request_id = self._request_by_session.get(session_id)
            record = self._active_by_request.get(request_id) if request_id else None
            if self._closed or record is None:
                return
            try:
                questions = (
                    record.question_registry
                    or self._question_registry_getter()
                )
                pending = next(
                    (
                        question for question in questions.list_pending(session_id)
                        if getattr(question, "id", None) == question_id
                    ), None,
                )
            except Exception:
                return
            registry_owner = getattr(pending, "execution_id", "") or ""
            if not registry_owner or registry_owner != record.execution_id:
                return
            if (
                type(payload_execution_id) is str
                and payload_execution_id
                and payload_execution_id != registry_owner
            ):
                return
            try:
                owns_session = self._acquire_cancel_cleanup(
                    session_id, record.thread_cancel
                )
            except Exception:
                owns_session = False
            if not owns_session:
                return
            try:
                questions.resolve(question_id, "declined", None)
            except Exception:
                return
            finally:
                _best_effort(
                    self._release_cancel_cleanup, session_id, record.thread_cancel
                )

    def _unregister_once(self, record: ActiveMCPRequest) -> None:
        with self._active_lock:
            self._registered_requests.pop(record.request_id, None)

    def _remove_owned(self, record: ActiveMCPRequest) -> bool:
        removed = False
        with self._active_lock:
            if self._active_by_request.get(record.request_id) is not record:
                return False
            self._active_by_request.pop(record.request_id, None)
            if self._request_by_session.get(record.session_id) == record.request_id:
                self._request_by_session.pop(record.session_id, None)
            removed = True
        if removed:
            with self._process_session_lock:
                if self._process_session_owners.get(record.session_id) is record:
                    self._process_session_owners.pop(record.session_id, None)
        return removed

    @classmethod
    def _claim_process_session(cls, record: ActiveMCPRequest) -> bool:
        with cls._process_session_lock:
            current = cls._process_session_owners.get(record.session_id)
            if current is not None and current is not record:
                return False
            cls._process_session_owners[record.session_id] = record
            return True

    def _worker_finished(self, record: ActiveMCPRequest) -> None:
        """Release an abandoned request only after its worker has returned."""
        record.worker_done.set()
        if record.outer_abandoned.is_set():
            self._remove_owned(record)
            self._unregister_once(record)

    def _audit_cancellation(self, record: ActiveMCPRequest, reason: str) -> None:
        from openprogram.events import make_event

        indicator = (
            reason
            if reason in {"prompt_cancel", "request_cancelled", "connection_closed"}
            else "request_cancelled"
        )
        event = make_event(
            "mcp.request.cancelled",
            "system",
            {
                "request_id": record.request_id,
                "execution_id": record.execution_id,
                "session_id": record.session_id,
                "client_id": record.client_id,
                "reason": indicator,
            },
            {"session": record.session_id},
        )
        _best_effort(self._event_bus.emit, event)

    async def _cancel_record(self, record: ActiveMCPRequest, *, reason: str) -> bool:
        with self._active_lock:
            if (
                self._active_by_request.get(record.request_id) is not record
                or self._request_by_session.get(record.session_id)
                != record.request_id
                or record.session_id in self._cleaning_sessions
            ):
                return False
            self._cleaning_sessions.add(record.session_id)
            execution_id = record.execution_id
            if not execution_id:
                # Register the pending intent while holding the same lock as
                # worker admission publishes execution_id. This makes the
                # empty-identity window atomic with the worker's barrier.
                record.cancel_requested = True
                if not record.cancel_reason:
                    record.cancel_reason = reason
                if not record.cancel_command_id:
                    record.cancel_command_id = f"mcp-cancel:{uuid.uuid4().hex}"
            elif not record.cancel_command_id:
                record.cancel_requested = True
                record.cancel_reason = record.cancel_reason or reason
                record.cancel_command_id = f"mcp-cancel:{uuid.uuid4().hex}"
            expected_version = record.execution_status_version
            command_id = record.cancel_command_id
            reason_code = record.cancel_reason or reason
        try:
            if not execution_id:
                # Keep the pending intent, but always pass through the
                # finally block so a later prompt is not permanently blocked
                # by the cleaning-session guard.
                self._audit_cancellation(record, reason)
                return True
            try:
                if expected_version is None or not command_id:
                    return False
                result = self._cancel_execution(
                    execution_id,
                    command_id=command_id,
                    expected_version=expected_version,
                    actor=dict(self.context.authority),
                    reason_code=reason_code,
                )
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                return False

            if not isinstance(result, ObservedCancelSubmission):
                return False
            if not result.accepted:
                return False

            with self._active_lock:
                still_active = (
                    self._active_by_request.get(record.request_id) is record
                    and self._request_by_session.get(record.session_id)
                    == record.request_id
                )
            if still_active:
                record.thread_cancel.set()
                record.tool_cancel.set()
            try:
                owns_session = self._acquire_cancel_cleanup(
                    record.session_id, record.thread_cancel
                )
            except Exception:
                owns_session = False
            try:
                if owns_session:
                    try:
                        questions = (
                            record.question_registry
                            or self._question_registry_getter()
                        )
                    except Exception:
                        questions = None
                    if questions is not None:
                        cancel_questions = getattr(
                            questions, "cancel_execution", None
                        )
                        if callable(cancel_questions):
                            _best_effort(
                                cancel_questions,
                                record.session_id,
                                record.execution_id,
                            )
                self._unregister_once(record)
            finally:
                if owns_session:
                    _best_effort(
                        self._release_cancel_cleanup,
                        record.session_id,
                        record.thread_cancel,
                    )
            self._remove_owned(record)
            self._audit_cancellation(record, reason)
            return True
        finally:
            with self._active_lock:
                self._cleaning_sessions.discard(record.session_id)

    async def cancel_request(self, request_id: str, *, reason: str) -> bool:
        with self._active_lock:
            record = self._active_by_request.get(request_id)
        return bool(
            record is not None and await self._cancel_record(record, reason=reason)
        )

    async def prompt_cancel(self, session_id: str) -> AgentToolResult:
        with self._active_lock:
            request_id = self._request_by_session.get(session_id)
            record = self._active_by_request.get(request_id) if request_id else None
        cancelled = bool(
            record is not None
            and await self._cancel_record(record, reason="prompt_cancel")
        )
        return json_result({"session_id": session_id, "cancelled": cancelled})

    def _begin_close(self) -> tuple[str, ...]:
        with self._active_lock:
            if self._closed:
                return ()
            self._closed = True
            unsubscribe = self._unsubscribe_questions
            self._unsubscribe_questions = None
            request_ids = tuple(self._active_by_request)
        if unsubscribe is not None:
            _best_effort(unsubscribe)
        return request_ids

    async def _finish_close(self, request_ids: tuple[str, ...]) -> None:
        await asyncio.gather(*(
            self.cancel_request(request_id, reason="connection_closed")
            for request_id in request_ids
        ))
        _best_effort(
            self._web_use_release_owner,
            self._web_use_owner_id,
        )

    async def aclose(self) -> None:
        """Await canonical cancellation before releasing MCP-owned state."""
        request_ids = self._begin_close()
        await self._finish_close(request_ids)

    def close(self) -> None:
        """Synchronously close only when no event loop is running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            request_ids = self._begin_close()
            asyncio.run(self._finish_close(request_ids))
        else:
            raise RuntimeError("MCPService.close() requires await aclose()")

    async def web_use_call(
        self,
        arguments: Mapping[str, Any],
        *,
        call_id: str,
        cancel_event: asyncio.Event,
    ) -> AgentToolResult:
        """Run the narrow browser-control route owned by this MCP connection."""
        authority = mcp_web_control_authority(self.context.authority)
        decision = decide_capability(authority, "browser.control")
        if not decision.allowed:
            return AgentToolResult(
                content=[TextContent(text="[denied] browser control unavailable")],
                details={"denied": True, "reason_code": decision.reason_code},
                is_error=True,
            )
        if cancel_event.is_set():
            return _execution_error()
        copied = _copy_json(arguments)
        state_lock = threading.Lock()
        state: dict[str, Any] = {
            "abandoned": False,
            "cleaned": False,
            "ready": False,
            "raw": None,
        }

        def cleanup_once(raw: Any) -> None:
            with state_lock:
                if state["cleaned"]:
                    return
                state["cleaned"] = True
            session_id = _web_session_id(raw, copied)
            page_tokens = _web_page_context_tokens(raw)
            if session_id and copied.get("command") != "close":
                _best_effort(
                    lambda: self._web_use_dispatch(
                        {"command": "close", "web_session_id": session_id},
                        owner_id=self._web_use_owner_id,
                    )
                )
            if page_tokens:
                _best_effort(
                    self._web_use_release_pages,
                    self._web_use_owner_id,
                    page_tokens,
                )
            if self._closed:
                _best_effort(
                    self._web_use_release_owner,
                    self._web_use_owner_id,
                )

        def abandon() -> None:
            with state_lock:
                state["abandoned"] = True
                ready = state["ready"]
                raw = state["raw"]
            if ready:
                cleanup_once(raw)

        def dispatch() -> Any:
            try:
                raw = self._web_use_dispatch(
                    copied, owner_id=self._web_use_owner_id,
                )
            except Exception:
                if self._closed:
                    _best_effort(
                        self._web_use_release_owner,
                        self._web_use_owner_id,
                    )
                raise
            with state_lock:
                state["raw"] = raw
                state["ready"] = True
                needs_cleanup = state["abandoned"] or self._closed
            if needs_cleanup:
                cleanup_once(raw)
            return raw

        try:
            raw = await anyio.to_thread.run_sync(dispatch)
            if cancel_event.is_set() or self._closed:
                abandon()
                return _execution_error()
            if isinstance(raw, AgentToolResult):
                return raw
            from openprogram.programs._runtime import _normalize_result
            return _normalize_result(
                raw, call_id=call_id, max_chars=100_000,
                persist_full=False, head_ratio=0.7,
            )
        except asyncio.CancelledError:
            abandon()
            raise
        except Exception:
            return _execution_error()

    async def prompt_send(
        self,
        prompt: str,
        *,
        session_id: str | None,
        request_id: str,
    ) -> AgentToolResult:
        from openprogram.agent.dispatcher import TurnRequest
        from openprogram.programs.permission_rule import load_merged_rules

        selected_session_id = session_id
        agent_id = "main"
        record = None
        registered = False
        if selected_session_id is None:
            with self._active_lock:
                if self._closed or request_id in self._active_by_request:
                    return json_result(
                        {"error": "prompt execution failed"}, is_error=True
                    )
                try:
                    for _ in range(8):
                        candidate = f"mcp_{uuid.uuid4().hex}"
                        if self._session_db.get_session(candidate) is None:
                            selected_session_id = candidate
                            break
                    if selected_session_id is None:
                        raise RuntimeError
                    thread_cancel = threading.Event()
                    tool_cancel = asyncio.Event()
                    user_msg_id = uuid.uuid4().hex[:12]
                    record = ActiveMCPRequest(
                        request_id=request_id,
                        session_id=selected_session_id,
                        client_id=self.context.client_id,
                        thread_cancel=thread_cancel,
                        tool_cancel=tool_cancel,
                    )
                    self._session_db.create_session(
                        selected_session_id, "main", source="mcp"
                    )
                    if not self._claim_process_session(record):
                        raise RuntimeError("MCP session is already active")
                    registered = True
                    self._active_by_request[request_id] = record
                    self._request_by_session[selected_session_id] = request_id
                    self._registered_requests[request_id] = record
                except Exception:
                    registered = False
            if not registered:
                return json_result({"error": "prompt execution failed"}, is_error=True)
        else:
            invalid_session = False
            try:
                row = self._session_db.get_session(selected_session_id)
                if (
                    not isinstance(row, Mapping)
                    or row.get("id") != selected_session_id
                    or type(row.get("agent_id")) is not str
                    or not row["agent_id"]
                ):
                    raise ValueError
                agent_id = row["agent_id"]
            except Exception:
                invalid_session = True
            if invalid_session:
                raise _mcp_error(mcp_types.INVALID_PARAMS, "invalid MCP prompt session")

            thread_cancel = threading.Event()
            tool_cancel = asyncio.Event()
            record = ActiveMCPRequest(
                request_id=request_id,
                session_id=selected_session_id,
                client_id=self.context.client_id,
                thread_cancel=thread_cancel,
                tool_cancel=tool_cancel,
            )
            with self._active_lock:
                if (
                    self._closed
                    or request_id in self._active_by_request
                    or selected_session_id in self._request_by_session
                    or selected_session_id in self._cleaning_sessions
                ):
                    return json_result(
                        {"error": "prompt execution failed"}, is_error=True
                    )
                registered = True
                if registered:
                    if not self._claim_process_session(record):
                        return json_result(
                            {"error": "prompt execution failed"}, is_error=True
                        )
                    self._active_by_request[request_id] = record
                    self._request_by_session[selected_session_id] = request_id
                    self._registered_requests[request_id] = record
            if not registered:
                return json_result({"error": "prompt execution failed"}, is_error=True)

        user_msg_id = uuid.uuid4().hex[:12]
        try:
            request = TurnRequest(
                session_id=selected_session_id,
                user_text=prompt,
                agent_id=agent_id,
                source="mcp",
                permission_mode="ask",
                permission_rules=load_merged_rules(selected_session_id),
                user_msg_id=user_msg_id,
                **dict(self.context.authority),
            )
        except Exception:
            self._remove_owned(record)
            self._unregister_once(record)
            return json_result({"error": "prompt execution failed"}, is_error=True)
        failure = None
        try:
            result = await anyio.to_thread.run_sync(
                lambda: self._run_worker(record, request),
                abandon_on_cancel=True,
            )
        except asyncio.CancelledError:
            record.outer_abandoned.set()
            await asyncio.shield(
                self.cancel_request(request_id, reason="request_cancelled")
            )
            raise
        except Exception:
            failure = json_result({"error": "prompt execution failed"}, is_error=True)
        finally:
            if not record.outer_abandoned.is_set() or record.worker_done.is_set():
                self._remove_owned(record)
                self._unregister_once(record)
        if thread_cancel.is_set() or tool_cancel.is_set():
            raise asyncio.CancelledError
        if failure is not None:
            return failure
        return prompt_result(selected_session_id, result)

    def _run_worker(self, record: ActiveMCPRequest, request: Any) -> Any:
        registered = False
        try:
            from openprogram.agent.production_driver import CanonicalAgentAdapter

            try:
                question_registry = self._question_registry_getter()
            except Exception:
                # Question observation is auxiliary to turn execution. A
                # broken registry hook must not prevent the admitted prompt
                # from running or change its ownership outcome.
                question_registry = None
            record.question_registry = question_registry
            adapter = CanonicalAgentAdapter(
                turn_runner=lambda *, request, cancel_event: self._process_user_turn(
                    request, cancel_event=cancel_event,
                ),
                question_registry=question_registry,
            )
            admission = adapter.admit(
                request,
                trusted_actor=dict(self.context.authority),
                user_message_id=request.user_msg_id,
                config_snapshot_ref=f"mcp:{record.client_id}",
            )
            with self._active_lock:
                record.execution_id = admission.execution_id
                record.execution_status_version = admission.status_version
                cancel_requested = record.cancel_requested
                cancel_reason = record.cancel_reason or "request_cancelled"
                if not cancel_requested:
                    # Publish the identity and close the admission barrier in
                    # one lock acquisition. A cancel arriving after this
                    # point is a normal exact execution cancellation.
                    record.activation_started = True
            if cancel_requested:
                if asyncio.run(self._cancel_record(record, reason=cancel_reason)):
                    return None
                # The durable cancellation was rejected or unavailable.  Do
                # not fabricate a local cancellation; continue the admitted
                # turn until a later protocol intent succeeds.
                with self._active_lock:
                    if self._active_by_request.get(record.request_id) is record:
                        record.activation_started = True
            self._register_cancel_event(
                record.session_id,
                record.thread_cancel,
                execution_id=record.execution_id,
            )
            registered = True
            _active, result = asyncio.run(adapter.activate(admission))
            return result
        finally:
            if registered and record.execution_id:
                _best_effort(
                    self._unregister_cancel_event,
                    record.session_id,
                    record.thread_cancel,
                    execution_id=record.execution_id,
                )
            self._worker_finished(record)

    def sessions_list(self) -> AgentToolResult:
        try:
            rows = self._session_db.list_sessions(limit=100)
            if not isinstance(rows, list):
                raise ValueError
            payload = [_session_row(row) for row in rows]
        except Exception:
            return json_result({"error": "session data unavailable"}, is_error=True)
        return json_result(payload)

    def session_get(self, session_id: str) -> AgentToolResult:
        try:
            session = self._session_db.get_session(session_id)
        except Exception:
            return json_result({"error": "session data unavailable"}, is_error=True)
        if session is None:
            return json_result({"error": "session not found"}, is_error=True)
        if not isinstance(session, Mapping) or session.get("id") != session_id:
            return json_result({"error": "session data unavailable"}, is_error=True)
        try:
            rows = self._session_db.get_branch(session_id)
            if not isinstance(rows, list):
                raise ValueError
            payload = [_message_row(row) for row in rows]
        except Exception:
            return json_result({"error": "session data unavailable"}, is_error=True)
        return json_result(payload)

    def exposed_runtime_tools(self) -> tuple[AgentTool, ...]:
        try:
            config = self._config_getter()
            server = config.get("mcp_server", {})
            configured = server.get("exposed_tools", [])
            if not isinstance(configured, list) or not all(
                isinstance(name, str) for name in configured
            ):
                return ()
            exposed = self._registry_exposed_names()
        except Exception:
            return ()

        tools: list[AgentTool] = []
        seen: set[str] = set()
        for name in tuple(configured):
            if name in seen:
                continue
            seen.add(name)
            if name not in exposed:
                continue
            try:
                tool = self._registry_get(name)
                allowed = decide_tool_authority(self.context.authority, name).allowed
            except Exception:
                continue
            if tool is not None and tool.name == name and allowed:
                try:
                    tools.append(tool.model_copy(deep=True))
                except Exception:
                    continue
        return tuple(tools)

    def tools_list(self) -> AgentToolResult:
        return json_result(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters,
                }
                for tool in self.exposed_runtime_tools()
            ]
        )

    async def tool_call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str,
        cancel_event: asyncio.Event,
        on_progress: Callable[[str], None] | None,
        require_exposed: bool = True,
    ) -> AgentToolResult:
        """Execute one currently exposed Runtime tool under fixed MCP authority."""
        tool: AgentTool | None = None
        try:
            config = self._config_getter()
            server = config.get("mcp_server", {})
            configured = server.get("exposed_tools", [])
            exposed = self._registry_exposed_names()
            if (
                not isinstance(configured, list)
                or not all(isinstance(item, str) for item in configured)
                or (require_exposed and name not in configured)
                or name not in exposed
            ):
                raise LookupError
            tool = self._registry_get(name)
            if tool is None or tool.name != name:
                raise LookupError
        except Exception:
            tool = None
        if tool is None:
            raise _mcp_error(
                mcp_types.METHOD_NOT_FOUND,
                "underlying Runtime tool not found",
            )

        invalid_arguments = False
        try:
            copied_arguments = _copy_json(arguments)
            if not isinstance(copied_arguments, dict):
                raise TypeError
            Draft202012Validator.check_schema(tool.parameters)
            validator = Draft202012Validator(tool.parameters)
            if next(validator.iter_errors(copied_arguments), None) is not None:
                raise ValueError
        except Exception:
            invalid_arguments = True
        if invalid_arguments:
            raise _mcp_error(
                mcp_types.INVALID_PARAMS,
                "invalid underlying Runtime tool arguments",
            )

        from openprogram.agent.dispatcher import TurnRequest
        from openprogram.agent.internals._approval import wrap_with_approval
        from openprogram.programs.permission_rule import load_merged_rules

        underlying_started = False

        async def forward_execute(
            forwarded_call_id,
            forwarded_arguments,
            forwarded_cancel_event,
            forwarded_update_callback,
        ):
            nonlocal underlying_started
            underlying_started = True
            return await tool.execute(
                forwarded_call_id,
                forwarded_arguments,
                forwarded_cancel_event,
                forwarded_update_callback,
            )

        setup_failed = False
        try:
            req = TurnRequest(
                session_id="",
                user_text="",
                agent_id="main",
                source="mcp",
                permission_mode="ask",
                permission_rules=load_merged_rules(""),
                **dict(self.context.authority),
            )
            forwarding_tool = tool.model_copy(update={"execute": forward_execute})
            gated = wrap_with_approval(forwarding_tool, req, lambda _event: None)
        except Exception:
            setup_failed = True
        if setup_failed:
            return _execution_error()

        update_callback = None
        if on_progress is not None:

            def update_callback(update: Any) -> None:
                if not isinstance(update, str):
                    return
                try:
                    on_progress(update)
                except Exception:
                    return

        execution_failed = False
        try:
            result = await gated.execute(
                call_id,
                copied_arguments,
                cancel_event,
                update_callback,
            )
            if not isinstance(result, AgentToolResult):
                raise TypeError
            detached = result.model_copy(deep=True)
            to_mcp_content(detached)
            if detached.is_error:
                detached = (
                    _trusted_approval_denial(
                        detached,
                        request=req,
                        tool_name=name,
                        arguments=copied_arguments,
                    )
                    if not underlying_started
                    else None
                ) or _execution_error()
        except Exception:
            execution_failed = True
        if execution_failed:
            return _execution_error()
        return detached


__all__ = ["ActiveMCPRequest", "MCPClientContext", "MCPService"]
