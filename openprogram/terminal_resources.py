"""Single-use invocation tickets for the existing native Desktop terminals.

The native host owns the PTY; this module owns the authorized Agent invocation.
A terminal view and an Agent binding always refer to the same live instance.
"""
from __future__ import annotations

from collections import OrderedDict
from contextvars import copy_context
from dataclasses import dataclass
import copy
import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any, Callable

from fastapi import Request

ACTIONS = frozenset({"list", "open", "observe", "input", "interrupt", "release", "close", "share", "resize"})
MUTATIONS = frozenset({"open", "input", "interrupt", "release", "close", "share", "resize"})
MAX_INPUT_BYTES = 16_384
TTL_SECONDS = 8.0


@dataclass
class _Ticket:
    plan: dict[str, Any]
    valid: Callable[[], bool]
    expires: float


class TerminalTickets:
    """Bounded one-use capabilities. A failed claim is not a new grant."""

    def __init__(self, *, clock=time.monotonic, limit: int = 512):
        self.clock = clock
        self.limit = limit
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, plan: dict[str, Any], valid: Callable[[], bool]) -> str:
        with self._lock:
            now = self.clock()
            self._tickets = {k: v for k, v in self._tickets.items() if v.expires > now}
            if len(self._tickets) >= self.limit:
                raise RuntimeError("terminal_dispatch_busy")
            ticket = secrets.token_hex(32)
            self._tickets[ticket] = _Ticket(copy.deepcopy(plan), valid, now + TTL_SECONDS)
            return ticket

    def revoke(self, ticket: str) -> None:
        with self._lock:
            self._tickets.pop(ticket, None)

    def claim(self, ticket: str, window_id: str) -> dict[str, Any]:
        if not isinstance(ticket, str) or not re.fullmatch(r"[0-9a-f]{64}", ticket):
            raise PermissionError("invalid_terminal_ticket")
        with self._lock:
            entry = self._tickets.pop(ticket, None)
        if entry is None or entry.expires <= self.clock() or entry.plan["window_id"] != window_id:
            raise PermissionError("invalid_terminal_ticket")
        if not entry.valid():
            raise PermissionError("terminal_dispatch_revoked")
        return copy.deepcopy(entry.plan)


class TerminalResults:
    """Correlate one native receipt without trusting a browser's reply fields."""

    def __init__(self, *, limit: int = 128):
        self._pending: dict[str, tuple[str, threading.Event, dict]] = {}
        self._lock = threading.Lock()
        self.limit = limit

    def reserve(self, window_id: str) -> tuple[str, threading.Event, dict]:
        with self._lock:
            if len(self._pending) >= self.limit:
                raise RuntimeError("terminal_dispatch_busy")
            token = secrets.token_hex(32)
            ready, holder = threading.Event(), {}
            self._pending[token] = (window_id, ready, holder)
            return token, ready, holder

    def deliver(self, token: str, window_id: str, result: dict) -> None:
        if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
                or not isinstance(result, dict) or type(result.get("ok")) is not bool):
            raise ValueError("invalid_terminal_result")
        # A bounded JSON copy also detaches the receipt from caller mutations.
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 256_000:
            raise ValueError("terminal_result_too_large")
        with self._lock:
            entry = self._pending.get(token)
            if entry is None or entry[0] != window_id or "result" in entry[2]:
                raise PermissionError("invalid_terminal_result")
            entry[2]["result"] = json.loads(encoded)
            entry[1].set()

    def release(self, token: str) -> None:
        with self._lock:
            self._pending.pop(token, None)


_tickets = TerminalTickets()
_results = TerminalResults()
_receipts: OrderedDict[tuple[str, str], tuple[str, dict[str, Any]]] = OrderedDict()
_inflight: set[tuple[str, str]] = set()
_receipt_lock = threading.Lock()
_observed_owners: OrderedDict[str, tuple[str, str]] = OrderedDict()


def _retired_owners(window_id: str) -> list[str]:
    from openprogram.execution import default_store
    with _receipt_lock:
        candidates = list(_observed_owners.items())
    retired = []
    for owner, (wid, execution_id) in candidates:
        if wid != window_id:
            continue
        execution = default_store().get_execution(execution_id)
        status = getattr(getattr(execution, "status", None), "value", None)
        if status in {"completed", "failed", "cancelled", "interrupted"}:
            retired.append(owner)
    return retired


def _arguments(action, terminal_id, generation, binding_id, data, cursor, expected_input_revision, workdir, shared=None, cols=None, rows=None):
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError("unsupported_terminal_action")
    for name, value, limit in (("terminal_id", terminal_id, 256), ("generation", generation, 64), ("binding_id", binding_id, 64)):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"invalid_{name}")
    if type(cursor) is not int or cursor < 0 or cursor > 2**53 - 1:
        raise ValueError("invalid_cursor")
    if action not in {"list", "open"} and (not terminal_id or not re.fullmatch(r"[0-9a-f]{32}", generation)):
        raise ValueError("list_terminals_then_observe_an_exact_generation")
    if action in {"input", "interrupt", "close", "release", "share", "resize"} and not re.fullmatch(r"[0-9a-f]{32}", binding_id):
        raise ValueError("observe_required")
    if action in {"input", "interrupt", "close", "share", "resize"} and (
        type(expected_input_revision) is not int or expected_input_revision < 0 or expected_input_revision > 2**53 - 1
    ):
        raise ValueError("observe_required")
    if action == "input" and (not isinstance(data, str) or not data or len(data.encode("utf-8")) > MAX_INPUT_BYTES):
        raise ValueError("invalid_input")
    if workdir is not None and (not isinstance(workdir, str) or not workdir.strip() or "\0" in workdir):
        raise ValueError("invalid_workdir")
    if action == "share" and type(shared) is not bool:
        raise ValueError("invalid_scope")
    if action == "resize" and (type(cols) is not int or type(rows) is not int or not 20 <= cols <= 500 or not 5 <= rows <= 200):
        raise ValueError("invalid_size")
    return {"shared": shared, "cols": cols, "rows": rows, "action": action, "terminal_id": terminal_id, "generation": generation,
            "binding_id": binding_id, "data": data if action == "input" else "",
            "cursor": cursor, "expected_input_revision": expected_input_revision,
            "workdir": workdir}


def _execution_current(execution_id: str, session_id: str, version: int) -> bool:
    from openprogram.agent.run_control import is_worker_stopping
    from openprogram.execution import default_store
    if is_worker_stopping():
        return False
    execution = default_store().get_execution(execution_id)
    status = getattr(getattr(execution, "status", None), "value", None)
    return bool(execution and execution.session_id == session_id
                and execution.status_version == version and status == "running")


def _dispatch(plan: dict[str, Any], *, ws, window_id: str, revision: int, valid: Callable[[], bool]):
    from openprogram.webui.ws_actions import webtab

    def still_valid():
        try:
            connected = any(owner is ws and wid == window_id and rev == revision
                            for owner, wid, rev in webtab.registered_desktop_windows())
            return connected and valid()
        except Exception:
            return False

    if not still_valid():
        return {"ok": False, "error": "terminal_dispatch_revoked"}
    from openprogram.webui import server as server
    from openprogram.webui.ws_delivery import send_to_connection

    result_ticket, ready, holder = _results.reserve(window_id)
    ticket = None
    try:
        ticket = _tickets.issue({**plan, "result_ticket": result_ticket}, still_valid)
        payload = json.dumps({"type": "terminal.command", "data": {
            "ticket": ticket, "window_id": window_id,
        }})
        # Target the original authenticated connection. A disconnected or
        # replaced connection never receives an automatic replay.
        sent = send_to_connection(ws, payload, server._loop)
        if sent and ready.wait(12.0):
            return holder["result"]
        return {"ok": False, "error": "terminal_result_unconfirmed",
                "terminal_id": plan["terminal_id"], "operation_id": plan["operation_id"],
                "result_unconfirmed": True,
                "message": "Inspect the terminal before another input. This request was not replayed."}
    finally:
        if ticket is not None:
            _tickets.revoke(ticket)
        _results.release(result_ticket)


def execute(action: str, terminal_id: str = "", generation: str = "", binding_id: str = "",
            data: str = "", cursor: int = 0, expected_input_revision: int | None = None,
            workdir: str | None = None, shared: bool | None = None,
            cols: int | None = None, rows: int | None = None) -> dict[str, Any]:
    """Dispatch only from a live permitted local execution, never from a model identity."""
    from openprogram import sandbox
    from openprogram.agent import surface_context
    from openprogram.backend import get_active_backend
    from openprogram.execution import default_store
    from openprogram.processes import current_owner
    from openprogram.programs._runtime import current_tool_call_id
    from openprogram.webui.ws_actions import webtab

    args = _arguments(action, terminal_id, generation, binding_id, data, cursor, expected_input_revision, workdir, shared, cols, rows)
    if get_active_backend().backend_id != "local":
        return {"ok": False, "error": "desktop_terminal_requires_local_backend"}
    # Existing user shells cannot be retroactively sandboxed. Refuse rather than
    # changing a task's sandbox policy or granting an unrestricted fallback.
    if sandbox.resolve_policy() is not None:
        return {"ok": False, "error": "host_terminal_outside_active_sandbox",
                "message": "Use bash with workdir under this task's sandbox. Terminal does not bypass it."}
    session_id, execution_id, call_id = current_owner()
    if not execution_id:
        return {"ok": False, "error": "terminal_requires_live_execution"}
    execution = default_store().get_execution(execution_id)
    if execution is None or not _execution_current(execution_id, session_id, execution.status_version):
        return {"ok": False, "error": "terminal_execution_not_running"}
    context = surface_context.current() or {}
    window_id = str(context.get("origin_window_id") or context.get("window_id") or "")
    candidates = [(ws, wid, rev) for ws, wid, rev in webtab.registered_desktop_windows()
                  if not window_id or wid == window_id]
    if len(candidates) != 1:
        return {"ok": False, "error": "terminal_requires_one_originating_desktop_window"}
    ws, window_id, revision = candidates[0]
    owner_id = f"{session_id}:{execution_id}"
    operation_id = str(current_tool_call_id() or call_id or secrets.token_hex(16))
    key = (owner_id, operation_id)
    digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    with _receipt_lock:
        prior = _receipts.get(key)
        if prior:
            return {**prior[1], "receipt_replayed": True} if prior[0] == digest else {"ok": False, "error": "conflicting_operation"}
        if key in _inflight or len(_inflight) >= 128:
            return {"ok": False, "error": "terminal_dispatch_busy"}
        _inflight.add(key)
    result = {"ok": False, "error": "terminal_result_unconfirmed", "result_unconfirmed": True}
    try:
        if action == "open":
            from openprogram.programs.tools.files.bash.workdir import prepare_command
            from openprogram.worktree.context import current_worktree_path
            _, _, cwd = prepare_command("", workdir or ".", backend_id="local", worktree=current_worktree_path())
            args.update(terminal_id="terminal-resource:" + secrets.token_hex(16), workdir=cwd)
        version = execution.status_version
        invocation_context = copy_context()

        def valid():
            return invocation_context.run(lambda: sandbox.resolve_policy() is None
                                          and _execution_current(execution_id, session_id, version))

        plan = {**args, "session_id": session_id, "owner_id": owner_id, "window_id": window_id,
                "operation_id": operation_id, "retired_owners": _retired_owners(window_id),
                "deadline": int((time.time() + TTL_SECONDS) * 1000)}
        result = _dispatch(plan, ws=ws, window_id=window_id, revision=revision, valid=valid)
        if result.get("ok") and action in {"open", "observe"}:
            with _receipt_lock:
                _observed_owners[owner_id] = (window_id, execution_id)
                while len(_observed_owners) > 512:
                    _observed_owners.popitem(last=False)
        return result
    finally:
        with _receipt_lock:
            _inflight.discard(key)
            if action in MUTATIONS:
                _receipts[key] = (digest, copy.deepcopy(result))
                while len(_receipts) > 512:
                    _receipts.popitem(last=False)


def register_routes(app) -> None:
    """Native Desktop redeems and acknowledges. Browser cookies do not suffice."""
    from fastapi.responses import JSONResponse
    from openprogram.backend_endpoint import is_loopback_host

    def authorized(request: Request) -> bool:
        auth = getattr(request.app.state, "owner_auth", None)
        headers = request.headers.getlist("authorization")
        peer = request.client.host if request.client else ""
        return bool(is_loopback_host(peer) and len(headers) == 1
                    and headers[0].startswith("Bearer ") and auth is not None
                    and auth.verify_token(headers[0][7:]))

    async def read_body(request: Request, limit: int) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > limit:
                raise ValueError("request_too_large")
            raw.extend(chunk)
        body = json.loads(raw)
        if not isinstance(body, dict) or not isinstance(body.get("window_id"), str):
            raise ValueError("invalid_request")
        return body

    @app.post("/api/terminal/claim")
    async def claim_terminal_ticket(request: Request):
        if not authorized(request):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
        try:
            body = await read_body(request, 2048)
            plan = _tickets.claim(body.get("ticket"), body["window_id"])
            return JSONResponse({"ok": True, "plan": plan}, headers={"Cache-Control": "no-store"})
        except (ValueError, PermissionError, TypeError):
            return JSONResponse({"ok": False, "error": "invalid_or_revoked_ticket"}, status_code=403)

    @app.post("/api/terminal/result")
    async def receive_terminal_result(request: Request):
        if not authorized(request):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
        try:
            body = await read_body(request, 260_000)
            # Do not discard an already delivered effect just because the task
            # has since paused. Admission and completion are different events.
            _results.deliver(body.get("ticket"), body["window_id"], body.get("result"))
            return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        except (ValueError, PermissionError, TypeError):
            return JSONResponse({"ok": False, "error": "invalid_terminal_result"}, status_code=403)
