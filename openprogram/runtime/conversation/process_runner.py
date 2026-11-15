"""Run a turn's Agent method tools in an isolated subprocess.

The stop button can then SIGKILL the entire process group in milliseconds
without waiting for cooperative cancel points. This is conversation engine
code: the child rebuilds the turn's ``TurnRequest``, surface context and
session bindings before it runs the tool.

Why this exists: the chat-path / forced-tool-call wrapper used to run
the tool body on the worker's own thread. Canonical execution cancellation
can mark
the session cancelled and the Agent method pre-invocation hook
would eventually raise CancelledError — but only at the *next* hook
point, which for an agent in the middle of a vision call could be
800–1500ms away. Users compared this to Claude Code's instant stop
and asked for the same UX.

Design:
  - Parent calls ``run_agent_method_in_subprocess(...)``.
  - We fork (mp.get_context("fork")) so we inherit ContextVars,
    registry state, loaded modules — no re-import latency.
  - Child puts itself in its own process group (``os.setpgrp``) so
    ``os.killpg(pgid, SIGKILL)`` reaches every grandchild (e.g. a
    Playwright browser, an mcp server) the tool spawned.
  - Events the wrapper would normally emit (placeholder, result) are
    funneled through an ``mp.Queue`` and re-emitted on the parent
    side by a small drain thread, so the WS clients keep seeing the
    same envelopes as before.
  - Stop = parent looks up the live ``Process`` for the session and
    sends SIGKILL to its pgid. Result is "not written" → parent
    returns a killed marker.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import pickle
import queue
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from openprogram.runtime.conversation.surface_context import (
    page_cleanup_failure as _page_cleanup_failure,
)

_log = logging.getLogger(__name__)


# execution_id → live Process. Session is a secondary index so a
# compatibility session-level lookup still finds the current owner.
_active: dict[str, mp.Process] = {}
_active_stop_q: dict[str, "mp.Queue"] = {}
_active_lock = threading.Lock()

# Agentic tools that run under a wall-clock budget their caller may raise
# with ``max_seconds``. No shipped tool takes one today.
_BOUNDED_AGENTIC_TOOLS: set[str] = set()
_DEFAULT_AGENTIC_TIMEOUT_SECONDS = 300.0
AGENTIC_TIMEOUT_ENV = "OPENPROGRAM_AGENTIC_TIMEOUT_S"
_STOP_GRACE_MIN_SECONDS = 30.0
_STOP_GRACE_MAX_SECONDS = 120.0
_PROGRESS_STEPS = 12
_PROGRESS_RECENT = 8


def _default_agentic_timeout_seconds() -> float:
    raw = os.environ.get(AGENTIC_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_AGENTIC_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "%s=%r is not a number of seconds; using %g",
            AGENTIC_TIMEOUT_ENV, raw, _DEFAULT_AGENTIC_TIMEOUT_SECONDS,
        )
        return _DEFAULT_AGENTIC_TIMEOUT_SECONDS


def agentic_subprocess_timeout_seconds(
    tool_name: str, kwargs: dict | None,
) -> float | None:
    """Resolve the time budget of a bounded agentic call.

    An explicit ``max_seconds`` argument wins. Otherwise
    ``OPENPROGRAM_AGENTIC_TIMEOUT_S`` sets the default, which is 300 seconds
    when unset. Zero or a negative value means no budget.
    """
    if tool_name not in _BOUNDED_AGENTIC_TOOLS:
        return None
    raw = (kwargs or {}).get("max_seconds")
    value = _default_agentic_timeout_seconds() if raw is None else float(raw)
    return value if value > 0 else None


def agentic_stop_grace_seconds(budget_seconds: float) -> float:
    """Time an agent gets after its budget to stop and return its history."""
    return max(
        _STOP_GRACE_MIN_SECONDS,
        min(_STOP_GRACE_MAX_SECONDS, 0.2 * float(budget_seconds)),
    )


@dataclass(frozen=True)
class AgenticLimits:
    """How long one agentic call may run, and the arguments that enforce it.

    ``kwargs`` carries the budget as ``max_seconds`` so the agent stops on
    its own and returns its partial history. ``timeout_seconds`` is the
    later point where the parent kills the process if it has not stopped.
    """

    kwargs: dict
    budget_seconds: float | None
    timeout_seconds: float | None


def agentic_subprocess_limits(
    tool_name: str, kwargs: dict | None,
) -> AgenticLimits:
    args = dict(kwargs or {})
    budget = agentic_subprocess_timeout_seconds(tool_name, args)
    if budget is None:
        return AgenticLimits(args, None, None)
    if args.get("max_seconds") is None:
        args["max_seconds"] = budget
    return AgenticLimits(
        args, budget, budget + agentic_stop_grace_seconds(budget),
    )


def _stream_progress_line(data: dict) -> str:
    op = data.get("op")
    if data.get("kind") == "tool_ref" and op in {"block_started", "block_finished"}:
        state = "started" if op == "block_started" else (
            data.get("status") or "finished"
        )
        return f"tool {data.get('tool_name') or '?'} {state}"
    if op == "attempt_started":
        model = data.get("model") or data.get("provider") or ""
        return f"model call started{f' ({model})' if model else ''}"
    if op == "attempt_finished":
        return f"model call {data.get('status') or 'finished'}"
    if op == "node_finished":
        return f"call {data.get('status') or 'finished'}"
    return ""


def _tree_steps(tree: Any, limit: int) -> list[str]:
    """Flatten the latest execution tree into short, ordered step lines."""
    lines: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if not isinstance(node, dict):
            return
        if depth:
            line = f"{'  ' * (depth - 1)}{node.get('name') or 'node'}: {node.get('status') or '?'}"
            duration = node.get("duration_ms")
            if isinstance(duration, (int, float)):
                line += f" ({duration / 1000:.1f} s)"
            error = str(node.get("error") or "").strip()
            if error:
                line += f" - {error.splitlines()[0][:160]}"
            lines.append(line)
        if depth < 3:
            for child in node.get("children") or []:
                walk(child, depth + 1)

    walk(tree, 0)
    return lines[-limit:]


class _ChildProgress:
    """A bounded summary of what an agentic child reported while it ran.

    The parent keeps it so a call stopped at its deadline still says how far
    it got: the latest execution tree, the last stream events, and how long
    the child had been silent.
    """

    def __init__(self) -> None:
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._tree: Any = None
        self._recent: deque[str] = deque(maxlen=_PROGRESS_RECENT)
        self._events = 0
        self._last_event: float | None = None

    def record(self, env: Any) -> None:
        if not isinstance(env, dict):
            return
        now = time.monotonic()
        line = ""
        tree = None
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        if env.get("__op_webtab__"):
            command = data.get("command") if isinstance(data.get("command"), dict) else {}
            line = f"page command {command.get('op') or '?'}"
        elif env.get("__op_question__"):
            line = "asked the user a question"
        elif env.get("type") == "chat_response":
            if data.get("type") == "execution_stream":
                line = _stream_progress_line(data)
            elif data.get("type") == "tree_update":
                tree = data.get("tree")
        with self._lock:
            self._events += 1
            self._last_event = now
            if tree is not None:
                self._tree = tree
            if line:
                self._recent.append(f"{now - self.started:.1f} s {line}")

    def report(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            return {
                "elapsed_seconds": round(now - self.started, 1),
                "events": self._events,
                "seconds_since_last_event": (
                    None if self._last_event is None
                    else round(max(0.0, now - self._last_event), 1)
                ),
                "steps": _tree_steps(self._tree, _PROGRESS_STEPS),
                "recent": list(self._recent),
            }


def _timeout_message(
    tool_name: str,
    timeout: float,
    budget: float | None,
    progress: dict,
) -> str:
    lines = [f"agentic subprocess timed out after {timeout:g} seconds"]
    if budget is not None:
        lines.append(
            f"{tool_name} did not stop within its {budget:g}-second budget "
            f"plus {timeout - budget:g} seconds to return, so it was stopped "
            "and its partial result was lost."
        )
    else:
        lines.append(f"{tool_name} was stopped at its {timeout:g}-second limit.")
    if progress["steps"]:
        lines.append("Last known steps:")
        lines.extend(f"  {step}" for step in progress["steps"])
    if progress["recent"]:
        lines.append("Recent activity (seconds after start):")
        lines.extend(f"  {item}" for item in progress["recent"])
    silent = progress["seconds_since_last_event"]
    if silent is None:
        lines.append("The process reported no progress before it was stopped.")
    else:
        lines.append(f"Its last report came {silent:g} seconds before the stop.")
    if tool_name in _BOUNDED_AGENTIC_TOOLS:
        lines.append(
            f"To allow more time, set {AGENTIC_TIMEOUT_ENV} (seconds, 0 removes "
            f"the limit) or pass max_seconds to {tool_name}; for a long task, "
            "split it into smaller requests."
        )
    return "\n".join(lines)


def _new_child_webtab_bridge(event_queue):
    pending: dict[str, tuple[threading.Event, dict]] = {}
    lock = threading.Lock()

    def request(command: dict, timeout: float) -> dict:
        req_id = uuid.uuid4().hex
        event = threading.Event()
        holder: dict = {}
        with lock:
            pending[req_id] = (event, holder)
        try:
            event_queue.put({
                "__op_webtab__": True,
                "data": {
                    "req_id": req_id,
                    "command": command,
                    "timeout": timeout,
                },
            })
            if not event.wait(max(0.1, float(timeout)) + 1):
                return {
                    "ok": False,
                    "error": "timeout: parent worker did not answer webtab bridge",
                }
            return holder.get("result") or {
                "ok": False,
                "error": "empty parent webtab bridge reply",
            }
        finally:
            with lock:
                pending.pop(req_id, None)

    def handle_answer(message: dict) -> bool:
        if not isinstance(message, dict) or not message.get("__op_webtab_result__"):
            return False
        with lock:
            entry = pending.get(message.get("req_id") or "")
            if entry is not None:
                entry[1]["result"] = message.get("result")
                entry[0].set()
        return True

    return request, handle_answer


def _desktop_window_registration(desktop_link, window_id: str, *, sole: bool = False):
    registered = desktop_link.registered_desktop_windows()
    if window_id:
        return next((item for item in registered if item[1] == window_id), None)
    return registered[0] if sole and len(registered) == 1 else None


_WEBTAB_BRIDGE_MAX_TIMEOUT_SECONDS = 15.0
# One open plus two ownership-rollbacks can each use the bridge timeout;
# request_on_ws also has at most two seconds of bounded send overhead.
_WEBTAB_FINALIZE_TIMEOUT_SECONDS = 3 * (
    _WEBTAB_BRIDGE_MAX_TIMEOUT_SECONDS + 2.0
) + 1.0
_WEBTAB_DRAIN_STOP = "__op_webtab_drain_stop__"


def _bounded_page_close(request: Callable[[], dict]) -> dict:
    result: dict = {}
    for _ in range(2):
        try:
            candidate = request()
            result = candidate if isinstance(candidate, dict) else {
                "ok": False,
                "error": "desktop app returned an invalid Page close result",
            }
        except Exception as exc:
            result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if result.get("ok"):
            break
    return result


def _open_bridged_webtab(
    desktop_link,
    command: dict,
    timeout: float,
    *,
    allowed_window_id: str = "",
    tracked_pages: dict[str, dict] | None = None,
) -> dict:
    window_id = str(command.get("window_id") or "")
    background = command.get("background") is True
    if background and (
        not allowed_window_id or window_id != allowed_window_id
    ):
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "background Page open is outside the originating window",
        }
    if not window_id and not background:
        return desktop_link.request_open_tab(
            command.get("url") or "", timeout=timeout,
            **({"session_id": command["session_id"]} if command.get("session_id") else {}),
        )

    selected = _desktop_window_registration(desktop_link, window_id, sole=True)
    if selected is None:
        return {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "originating desktop window is unavailable",
        }
    owner_ws, selected_window_id, connection_revision = selected
    result = desktop_link.request_on_ws(owner_ws, command, timeout)
    if (
        background
        and isinstance(result, dict)
        and result.get("reason_code") == desktop_link.RESPONSE_TIMEOUT_REASON_CODE
    ):
        return _page_cleanup_failure(str(
            result.get("error")
            or "desktop Page creation timed out before cleanup was confirmed"
        ))
    if not isinstance(result, dict) or not result.get("ok"):
        return result if isinstance(result, dict) else {
            "ok": False, "error": "desktop app returned an invalid open result",
        }
    result = dict(result)
    result_window_id = str(result.get("window_id") or "")
    tab_id = str(result.get("tab_id") or "")
    target_id = str(result.get("target_id") or "")
    agent_owned = (
        desktop_link.validated_open_ownership(result).get("created") is True
    )

    def rollback_opened_page() -> dict | None:
        if not agent_owned:
            return None
        if not tab_id:
            return _page_cleanup_failure(
                "the opened Page identity could not be safely closed"
            )
        close_result = _bounded_page_close(lambda: desktop_link.request_on_ws(
            owner_ws,
            {
                "op": "close",
                "window_id": selected_window_id,
                "tab_id": tab_id,
            },
            timeout,
        ))
        if isinstance(close_result, dict) and close_result.get("ok"):
            return None
        if tracked_pages is not None:
            tracked_pages["unbound:" + uuid.uuid4().hex] = {
                "window_id": selected_window_id,
                "tab_id": tab_id,
                "agent_owned": True,
                "close_on_exit": True,
                "owner_ws": owner_ws,
                "cleanup_exhausted": True,
                "cleanup_error": str(
                    close_result.get("error") or "Page close was rejected"
                ),
            }
        return _page_cleanup_failure(
            str((close_result or {}).get("error") or "Page close was rejected")
            if isinstance(close_result, dict) else
            "desktop app returned an invalid Page close result"
        )

    if result_window_id != selected_window_id or not tab_id or not target_id:
        rollback_failure = rollback_opened_page()
        if rollback_failure is not None:
            return rollback_failure
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "desktop app returned another window or an incomplete Page",
        }
    binding_id = ""
    try:
        binding_id = desktop_link.register_binding(
            owner_ws,
            result_window_id,
            tab_id,
            target_id,
            geometry_revision=int(result.get("geometry_revision") or 0),
            allow_background=background,
            expected_connection_revision=connection_revision,
        )
        result["binding_id"] = binding_id
        page_key = desktop_link.binding_page_key(binding_id)
        if page_key:
            result["page_key"] = page_key
        result.update(desktop_link.binding_revisions(binding_id))
    except Exception:
        if binding_id:
            desktop_link.release_binding(binding_id)
        rollback_failure = rollback_opened_page()
        if rollback_failure is not None:
            return rollback_failure
        raise
    return result


def _close_bridged_webtab(
    desktop_link, command: dict, timeout: float, tracked_pages: dict[str, dict] | None,
) -> dict:
    binding_id = str(command.get("binding_id") or "")
    if binding_id:
        page = (tracked_pages or {}).get(binding_id)
        if tracked_pages is not None and (
            page is None or page.get("agent_owned") is not True
        ):
            return {
                "ok": False,
                "reason_code": "page_context_stale",
                "error": "borrowed Page binding cannot be closed",
            }
        result = _bounded_page_close(
            lambda: desktop_link.request_close_tab(binding_id, timeout=timeout)
        )
        if not result.get("ok") and page is not None:
            page["cleanup_exhausted"] = True
            page["cleanup_error"] = str(
                result.get("error") or "Page close was rejected"
            )
        return result
    window_id = str(command.get("window_id") or "")
    tab_id = str(command.get("tab_id") or "")
    if not window_id or not tab_id:
        return {"ok": False, "error": "exact Page close requires window and tab"}
    owned_binding = next((
        key for key, page in (tracked_pages or {}).items()
        if page.get("agent_owned") is True
        and page.get("window_id") == window_id
        and page.get("tab_id") == tab_id
    ), "")
    if not owned_binding:
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "exact Page close is not owned by this agent run",
        }
    page = tracked_pages[owned_binding]
    owner_ws = (
        desktop_link.binding_connection(owned_binding) or page.get("owner_ws")
    )
    if owner_ws is None:
        result = {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "originating desktop window is unavailable",
        }
    else:
        result = _bounded_page_close(
            lambda: desktop_link.request_on_ws(owner_ws, command, timeout)
        )
    if not result.get("ok"):
        page["cleanup_exhausted"] = True
        page["cleanup_error"] = str(
            result.get("error") or "Page close was rejected"
        )
    return result


def _update_tracked_webtabs(
    desktop_link,
    command: dict,
    result: dict,
    tracked_pages: dict[str, dict] | None,
    allowed_bindings: set[str] | None = None,
) -> None:
    if tracked_pages is None or not result.get("ok"):
        return
    if command.get("op") == "open":
        binding_id = str(result.get("binding_id") or "")
        if binding_id:
            owner_ws = desktop_link.binding_connection(binding_id)
            agent_owned = (
                desktop_link.validated_open_ownership(result).get("created") is True
            )
            tracked_pages[binding_id] = {
                **({"session_id": command["session_id"]} if command.get("session_id") else {}),
                "window_id": str(result.get("window_id") or ""),
                "tab_id": str(result.get("tab_id") or ""),
                "agent_owned": agent_owned,
                "close_on_exit": (
                    agent_owned and command.get("background") is True
                ),
                **({"owner_ws": owner_ws} if owner_ws is not None else {}),
            }
            if allowed_bindings is not None:
                allowed_bindings.add(binding_id)
        return
    if command.get("op") != "close":
        return
    binding_id = str(command.get("binding_id") or "")
    if binding_id:
        tracked_pages.pop(binding_id, None)
        if allowed_bindings is not None:
            allowed_bindings.discard(binding_id)
        return
    window_id = str(command.get("window_id") or "")
    tab_id = str(command.get("tab_id") or "")
    for key in [
        key for key, value in tracked_pages.items()
        if value.get("agent_owned") is True
        and value.get("window_id") == window_id
        and value.get("tab_id") == tab_id
    ]:
        desktop_link.release_binding(key)
        tracked_pages.pop(key, None)
        if allowed_bindings is not None:
            allowed_bindings.discard(key)


def _capture_bridged_pages(
    command: dict,
    tracked_pages: dict[str, dict] | None,
    *,
    allowed_window_id: str,
    allowed_bindings: set[str] | None,
) -> dict:
    """Capture Pages in the parent, where renderer bindings are authoritative."""
    from openprogram.runtime.conversation import surface_context
    from openprogram.host.resources import desktop_link

    binding_id = str(command.get("binding_id") or "")
    requested_window_id = str(command.get("window_id") or "")
    requested_tab_id = str(command.get("tab_id") or "")
    if binding_id:
        if allowed_bindings is not None and binding_id not in allowed_bindings:
            return {
                "ok": False,
                "reason_code": "page_context_stale",
                "error": "Page inventory binding is outside this agent run",
            }
        source = {
            "origin_window_id": allowed_window_id,
            "origin_tab_id": requested_tab_id,
            "surfaces": [{
                "binding_id": binding_id,
                **({"tab_id": requested_tab_id} if requested_tab_id else {}),
            }],
        }
    else:
        if (
            not allowed_window_id
            or requested_window_id != allowed_window_id
        ):
            return {
                "ok": False,
                "reason_code": "page_context_stale",
                "error": "Page inventory is outside the originating window",
            }
        source = surface_context.window_context(
            allowed_window_id,
            preferred_tab_id=requested_tab_id,
        )

    try:
        captured = surface_context.capture_pages(source)
    except surface_context.DesktopUnavailableError:
        # A binding revoked by a Desktop reconnect is not the end of the run:
        # list the same originating window again, never another window.
        if not binding_id or not allowed_window_id:
            raise
        captured = surface_context.capture_pages(surface_context.window_context(
            allowed_window_id,
            preferred_tab_id=requested_tab_id,
        ))
    surfaces = [
        item for item in captured.get("surfaces") or []
        if isinstance(item, dict)
    ]
    captured_bindings = {
        str(item.get("binding_id") or "")
        for item in surfaces
        if item.get("binding_id")
    }
    captured_window_id = str(captured.get("window_id") or "")
    captured_windows = [
        str(item.get("window_id") or "")
        for item in captured.get("windows") or []
        if isinstance(item, dict)
    ]
    if (
        captured_window_id != allowed_window_id
        or any(window != allowed_window_id for window in captured_windows)
        or any(
            str(item.get("window_id") or captured.get("window_id") or "")
            != allowed_window_id
            for item in surfaces
        )
    ):
        for current in captured_bindings:
            desktop_link.release_binding(current)
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "Page inventory returned another desktop window",
        }

    if tracked_pages is None:
        for current in captured_bindings:
            desktop_link.release_binding(current)
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "parent Page binding tracker is unavailable",
        }
    for item in surfaces:
        current = str(item.get("binding_id") or "")
        if not current:
            continue
        owner_ws = desktop_link.binding_connection(current)
        tracked_pages[current] = {
            "window_id": str(
                item.get("window_id") or captured.get("window_id") or ""
            ),
            "tab_id": str(item.get("tab_id") or ""),
            "agent_owned": False,
            "close_on_exit": False,
            **({"owner_ws": owner_ws} if owner_ws is not None else {}),
        }
        if allowed_bindings is not None:
            allowed_bindings.add(current)
    return {"ok": True, "context": captured}


def _bridge_webtab_to_parent(
    data: dict,
    answer_queue,
    tracked_pages: dict[str, dict] | None = None,
    *,
    allowed_window_id: str = "",
    allowed_bindings: set[str] | None = None,
    session_id: str = "",
) -> dict:
    from openprogram.runtime.execution.run_control import (
        set_current_session_id,
        reset_current_session_id,
    )

    token = set_current_session_id(session_id)
    try:
        command = data.get("command") if isinstance(data, dict) else None
        # Attribution comes from the parent execution, never from child-supplied data.
        if isinstance(command, dict):
            command = {key: value for key, value in command.items() if key != "session_id"}
            if session_id:
                command["session_id"] = session_id
        op = command.get("op") if isinstance(command, dict) else ""
        valid = op in {"open", "active", "capture_pages"} or (
            op in {"activate", "screenshot"}
            and isinstance(command.get("binding_id"), str)
        ) or (
            op == "close" and any(
                isinstance(command.get(key), str)
                for key in ("binding_id", "tab_id")
            )
        )
        if not valid:
            result = {"ok": False, "error": "unsupported webtab bridge operation"}
        else:
            try:
                timeout = max(0.1, min(
                    float(data.get("timeout", 15)),
                    _WEBTAB_BRIDGE_MAX_TIMEOUT_SECONDS,
                ))
                from openprogram.host.resources import desktop_link

                binding_id = str(command.get("binding_id") or "")
                binding_authorized = (
                    allowed_bindings is None or binding_id in allowed_bindings
                )
                if (
                    op in {"activate", "screenshot", "close"}
                    and binding_id
                    and not binding_authorized
                ):
                    result = {
                        "ok": False,
                        "reason_code": "page_context_stale",
                        "error": "Page binding is outside this agent run",
                    }
                elif op == "activate":
                    result = desktop_link.request_bound_tab(
                        command["binding_id"],
                        url=command.get("url") or "",
                        timeout=timeout,
                        expected_page_revision=int(
                            command.get("expected_page_revision") or 0
                        ),
                        expected_access_revision=int(
                            command.get("expected_access_revision") or 0
                        ),
                        expected_geometry_revision=int(
                            command.get("expected_geometry_revision") or 0
                        ),
                    )
                elif op == "screenshot":
                    result = desktop_link.request_bound_screenshot(
                        command["binding_id"],
                        timeout=timeout,
                        expected_page_revision=int(
                            command.get("expected_page_revision") or 0
                        ),
                        expected_access_revision=int(
                            command.get("expected_access_revision") or 0
                        ),
                        expected_geometry_revision=int(
                            command.get("expected_geometry_revision") or 0
                        ),
                    )
                elif op == "close":
                    result = _close_bridged_webtab(
                        desktop_link, command, timeout, tracked_pages,
                    )
                elif op == "open":
                    result = _open_bridged_webtab(
                        desktop_link,
                        command,
                        timeout,
                        allowed_window_id=allowed_window_id,
                        tracked_pages=tracked_pages,
                    )
                elif op == "capture_pages":
                    result = _capture_bridged_pages(
                        command,
                        tracked_pages,
                        allowed_window_id=allowed_window_id,
                        allowed_bindings=allowed_bindings,
                    )
                else:
                    result = desktop_link.request(command, timeout)
                _update_tracked_webtabs(
                    desktop_link,
                    command,
                    result,
                    tracked_pages,
                    allowed_bindings,
                )
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                from openprogram.runtime.conversation.surface_context import DesktopUnavailableError
                if isinstance(exc, DesktopUnavailableError):
                    # The child waits for its window instead of failing.
                    result["reason_code"] = "desktop_unavailable"
        answer_queue.put({
            "__op_webtab_result__": True,
            "req_id": data.get("req_id") if isinstance(data, dict) else None,
            "result": result,
        })
        return result
    finally:
        reset_current_session_id(token)


def _cleanup_bridged_webtabs(tracked_pages: dict[str, dict]) -> list[dict]:
    """Close child-created Pages and release every parent-owned binding."""
    from openprogram.host.resources import desktop_link

    def failure(binding_id: str, page: dict, error: str) -> dict:
        return {
            "binding_id": binding_id,
            "window_id": str(page.get("window_id") or ""),
            "tab_id": str(page.get("tab_id") or ""),
            "error": error,
        }

    failures = []
    for binding_id, page in list(tracked_pages.items()):
        if page.get("close_on_exit") is True:
            if page.get("cleanup_exhausted") is True:
                failures.append(failure(
                    binding_id,
                    page,
                    str(
                        page.get("cleanup_error")
                        or "Page close remained rejected after bounded retry"
                    ),
                ))
                continue
            try:
                owner_ws = (
                    desktop_link.binding_connection(binding_id) or page.get("owner_ws")
                )
            except Exception as exc:
                failures.append(failure(
                    binding_id, page, f"{type(exc).__name__}: {exc}",
                ))
                continue
            if owner_ws is None:
                failures.append(failure(
                    binding_id, page, "Page owner is unavailable",
                ))
                continue
            close_result = _bounded_page_close(lambda: desktop_link.request_on_ws(
                owner_ws,
                {
                    "op": "close",
                    **({"session_id": page["session_id"]} if page.get("session_id") else {}),
                    "window_id": page.get("window_id"),
                    "tab_id": page.get("tab_id"),
                },
                timeout=5.0,
            ))
            if not close_result.get("ok"):
                error = str(
                    close_result.get("error") or "Page close was rejected"
                )
                page["cleanup_exhausted"] = True
                page["cleanup_error"] = error
                failures.append(failure(binding_id, page, error))
                continue
        desktop_link.release_binding(binding_id)
        tracked_pages.pop(binding_id, None)
    return failures


def _permission_rules_from_snapshot(snapshot: Optional[dict]):
    if snapshot is None:
        return None
    from openprogram.runtime.permissions.modes import PermissionRules
    return PermissionRules(
        allow=list(snapshot.get("allow") or []),
        deny=list(snapshot.get("deny") or []),
        ask=list(snapshot.get("ask") or []),
    )


# ---------------------------------------------------------------------------
# Child entry point
# ---------------------------------------------------------------------------

def _link_registration_modules() -> list[str]:
    """The modules whose import registered the parent's channel and speech links.

    The core never names an interface; it passes on whatever registered here
    so a spawned child can register the same implementations.
    """
    from openprogram.runtime import channel_link, speech_link

    modules: list[str] = []
    for link in (channel_link.current(), speech_link.current()):
        module = getattr(type(link), "__module__", None) if link is not None else None
        if module and module not in modules:
            modules.append(module)
    return modules


def _import_link_registrations(modules: list[str]) -> None:
    import importlib

    for module in modules:
        try:
            importlib.import_module(module)
        except Exception:
            logging.getLogger(__name__).warning(
                "link registration module %s did not import", module, exc_info=True,
            )


def _child_entry(
    tool_name: str,
    kwargs: dict,
    session_id: str,
    anchor_msg_id: str,
    work_dir: Optional[str],
    result_path: str,
    event_queue: "mp.Queue",
    parent_call_id: Optional[str] = None,
    answer_queue: "Optional[mp.Queue]" = None,
    stop_queue: "Optional[mp.Queue]" = None,
    response_format_snapshot: Optional[dict] = None,
    render_range: Optional[dict[str, int]] = None,
    usage_ctx_snapshot: Optional[dict] = None,
    sandbox_policy_snapshot: Optional[dict] = None,
    authority_snapshot: Optional[dict] = None,
    permission_rules_snapshot: Optional[dict] = None,
    surface_context_snapshot: Optional[dict] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    execution_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
    generation: Optional[int] = None,
    model_setup_error: Optional[dict] = None,
    original_owner_input=None,
    permission_mode_snapshot: Optional[str] = None,
    link_registration_modules: Optional[list[str]] = None,
    function_log_path: Optional[str] = None,
    function_policy: Optional[str] = None,
    detached: bool = False,
) -> None:
    # This process keeps a read-only view of the run tables. Its progress goes
    # in its own log; run state changes through its parent.
    from openprogram.runtime.execution.store.run_tables import this_process_only_reads_run_tables
    this_process_only_reads_run_tables()
    from openprogram.runtime.execution.worker_calls import SubprocessWorkerCalls, use_worker_calls
    to_worker = SubprocessWorkerCalls(event_queue.put)
    use_worker_calls(to_worker)
    # Detach into our own process group so ``killpg`` from the parent
    # takes down every grandchild (browser, subprocess providers, ...).
    try:
        os.setpgrp()
    except (AttributeError, OSError):
        # os.setpgrp is absent on Windows and raises EPERM for a session
        # leader; the child then shares the parent's group and killpg is weaker.
        _log.debug("child could not detach into its own process group", exc_info=True)

    # ``spawn`` starts with a fresh interpreter. Pin it to the parent's
    # effective policy before any tool/runtime is created; later config edits
    # cannot widen an already-running child.
    from openprogram.host.sandbox import install_policy_snapshot
    install_policy_snapshot(sandbox_policy_snapshot or {"enabled": False})
    # The interfaces the parent had registered on the core's links (chat
    # channels, speech) register themselves when their module is imported;
    # a fresh interpreter has none of them until it imports the same modules.
    _import_link_registrations(link_registration_modules or [])
    if surface_context_snapshot is not None:
        from openprogram.runtime.conversation.surface_context import bind as _bind_surface
        _bind_surface(surface_context_snapshot)

    # Restore the parent's UsageContext, then override call_kind/call_label
    # with this subprocess's actual identity. The snapshot carries the
    # parent's session_id (valuable — keeps attribution to the session that
    # triggered this tool), but the parent's call_kind is typically "chat"
    # which is wrong for an Agent method subprocess. Set it to "exec"
    # with the tool's name as call_label so metering can tell Programs apart.
    try:
        from openprogram.models.usage.context import (
            apply_snapshot as _apply_uctx,
            _current as _usage_cur,
        )
        _apply_uctx(usage_ctx_snapshot)
        from openprogram.models.usage.context import current_usage_context
        _parent = current_usage_context()
        from dataclasses import replace
        _usage_cur.set(replace(_parent,
            call_kind="exec",
            call_label=tool_name,
            session_id=_parent.session_id or session_id,
            parent_session_id=_parent.parent_session_id,
            agent_id=_parent.agent_id,
        ))
    except Exception:
        # Usage attribution is optional; the call runs unattributed.
        _log.debug("usage context not applied in tool subprocess %s", tool_name, exc_info=True)

    # --- durable user-input subprocess bridge: answer side ---
    # The parent only forwards a terminal durable-wait projection.  It never
    # accepts or resolves a question locally; the child wakes its local thread
    # and reads the canonical row itself.
    def handle_webtab_answer(_message):
        return False

    if answer_queue is not None:
        try:
            from openprogram.runtime.agent.questions import get_question_registry
            from openprogram.host.resources import desktop_link

            parent_request, handle_webtab_answer = _new_child_webtab_bridge(
                event_queue
            )
            desktop_link.install_parent_bridge(parent_request)

            def _answer_pump() -> None:
                reg = get_question_registry()
                while True:
                    try:
                        msg = answer_queue.get()
                    except Exception:
                        return
                    if msg is None:  # shutdown sentinel
                        return
                    if handle_webtab_answer(msg) or to_worker.handle_message(msg):
                        continue
                    try:
                        qid = msg.get("id")
                        if qid:
                            reg.deliver(qid, msg.get("outcome"), msg.get("value"))
                    except Exception:
                        # Pump thread must survive a malformed answer.
                        _log.debug("question answer not delivered: %r", msg, exc_info=True)

            threading.Thread(target=_answer_pump, daemon=True).start()
        except Exception:
            # Without the pump, runtime.ask questions in this child time out.
            _log.warning("question answer pump not started in tool subprocess", exc_info=True)
    # Marker so the wrapper inside the child uses orig_execute directly
    # instead of recursing into another subprocess.
    os.environ["OPENPROGRAM_IN_AGENTIC_SUBPROCESS"] = "1"
    # Spawn context: re-import openprogram so the agent_tools registry
    # populates in this fresh interpreter.
    try:
        import openprogram  # noqa: F401
        import openprogram.programs  # noqa: F401
        from openprogram.programs import agent_tools as _warm
        _warm()  # force registration
    except Exception:
        # The tool may still run; a missing registry shows up as an unknown tool.
        _log.warning("agent tool registry warm-up failed in subprocess", exc_info=True)

    # Re-install the session-scoped ContextVars. fork inherits the
    # snapshot, but we set them explicitly anyway so a spawn fallback
    # would still work.
    try:
        from openprogram.state.store import (
            SessionNodeWriter,
            _store as _store_var,
            _current_turn_id as _turn_id_var,
        )
        from openprogram.runtime.agent.call_state import (
            _current_runtime as _current_runtime_var,
        )
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.agent.runtime.factory import create_runtime
        from openprogram.programs._host.runtime import get as _get_tool
        from openprogram.runtime.conversation.turn_runtime import (
            _wrap_agentic_runtime_block,
        )
        from openprogram.runtime.agent.turn_request import TurnRequest
        from openprogram.runtime.execution.run_control import (
            set_current_session_id as _set_cid,
            set_current_execution_id as _set_eid,
        )

        # Start from a session store of this process's own, not one
        # inherited from the parent.
        try:
            import openprogram.runtime.context.session_db as _sdb_mod
            for attr in ("_default_db", "_DB_SINGLETON", "_db"):
                if hasattr(_sdb_mod, attr):
                    setattr(_sdb_mod, attr, None)
        except Exception:
            _log.debug("inherited session DB handle not reset in subprocess", exc_info=True)

        db = default_db()
        _store_var.set(SessionNodeWriter(db, session_id))
        _turn_id_var.set(anchor_msg_id)
        _set_cid(session_id)
        # spawn does not copy ContextVars; restore the process owner's exact
        # id so child-created questions carry the same cancellation owner.
        _set_eid(execution_id or parent_call_id or session_id)

        from openprogram.runtime.conversation.turn_runtime.turn_context import _unavailable_runtime
        if model_setup_error is not None:
            from openprogram.models.providers.utils.errors import ErrorReason, LLMError
            rt = _unavailable_runtime(LLMError(
                message=model_setup_error["message"],
                reason=ErrorReason(model_setup_error["reason"]),
                retryable=model_setup_error["retryable"],
            ))
        else:
            try:
                # Forced calls may omit selection entirely. This child must
                # never detect a global provider or fill a default model.
                if not provider or provider == "auto" or not model:
                    from openprogram.runtime.conversation.model_tools import _raise_model_unavailable
                    _raise_model_unavailable(None)
                rt = create_runtime(provider=provider, model=model)
            except Exception as error:
                rt = _unavailable_runtime(
                    error, model=f"{provider}/{model}" if provider and model else "unavailable",
                    provider=provider,
                )
        if response_format_snapshot is not None:
            from openprogram.runtime.agent.runtime import _current_response_format
            from openprogram.models.providers.structured_output import normalize_response_format
            _current_response_format.set(
                normalize_response_format(response_format_snapshot)
            )
        # --- user-input subprocess bridge: ask side ---
        # Send runtime.ask questions UP to the parent through ``event_queue``
        # (this child's own EventBus has no WS subscriber). The parent's drain
        # thread intercepts the ``__op_question__`` envelope, registers it on
        # the parent registry + draws the frontend card, and routes the answer
        # back via ``answer_queue`` (picked up by the answer-pump above).
        if answer_queue is not None and hasattr(rt, "set_question_transport"):
            try:
                from openprogram.runtime.agent.questions import QueueTransport
                rt.set_question_transport(QueueTransport(event_queue))
            except Exception:
                # Questions then use the child's own bus and time out unanswered.
                _log.warning("question transport not bridged for session %s", session_id, exc_info=True)
        if work_dir:
            try:
                abs_wd = os.path.abspath(os.path.expanduser(work_dir))
                os.makedirs(abs_wd, exist_ok=True)
                from openprogram.state.worktree.context import set_worktree
                set_worktree(abs_wd)
                if hasattr(rt, "set_workdir"):
                    rt.set_workdir(abs_wd)
            except Exception:
                # The tool runs in the inherited cwd.
                _log.warning("work dir %s not applied in subprocess for session %s", work_dir, session_id, exc_info=True)
        _current_runtime_var.set(rt)

        tool = _get_tool(tool_name)
        if tool is None:
            with open(result_path, "wb") as f:
                pickle.dump({"error": f"tool not found: {tool_name}"}, f)
            return

        from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
        req = TurnRequest(
            session_id=session_id,
            user_text="",
            agent_id="main",
            source="web",
            model_override=f"{provider}/{model}" if provider and model else None,
            render_range=render_range,
            surface_context=surface_context_snapshot,
            detached=detached,
            permission_mode=DEFAULT_PERMISSION if permission_mode_snapshot is None else permission_mode_snapshot,
            permission_rules=_permission_rules_from_snapshot(
                permission_rules_snapshot
            ),
            **(authority_snapshot or {}),
        )
        # Same context the dispatcher binds in-process: an inner
        # AgentSession created inside this tool inherits it.
        from openprogram.runtime.agent.turn_request import set_turn_request
        set_turn_request(req)
        from openprogram.runtime.conversation.turn_request_context import set_original_owner_input
        set_original_owner_input(original_owner_input)

        # Bridge child-side on_event into the parent via the queue.
        def _on_event(env: dict) -> None:
            try:
                event_queue.put(env, block=False)
            except (queue.Full, ValueError):
                # Queue.put(block=False) raises Full when full and ValueError once
                # closed; a dropped stream event is not worth failing the tool.
                _log.debug("child event dropped for session %s", session_id, exc_info=True)

        # Agentic functions run in a spawned process whose EventBus has no
        # Web subscriber. Forward the existing typed goal.update event through
        # the same parent event queue used by stream updates; this keeps Goal
        # state live without adding a Goal-specific transport.
        try:
            from openprogram.runtime.events import get_event_bus

            def _forward_goal_update(event) -> None:
                payload = dict(getattr(event, "payload", None) or {})
                _on_event({"type": "goal_update", "data": payload})

            get_event_bus().subscribe(
                _forward_goal_update,
                types={"goal.update"},
            )
        except Exception:
            # Goal state then refreshes only when the call finishes.
            _log.debug("goal update forwarding not installed for session %s", session_id, exc_info=True)

        # A native app window the child operates goes to the parent, which
        # owns the conversation's preview state and the App connection.
        try:
            from openprogram.host.resources.native_window import forward_to_parent
            forward_to_parent(lambda observed: _on_event({"__op_native_window__": True, "data": observed}))
        except Exception:
            # Optional preview: the parent just does not follow native windows.
            _log.debug("native window forwarding not installed for session %s", session_id, exc_info=True)
        # So does what the child does on a browser Page, for the preview to follow.
        try:
            from openprogram.programs.applications.browser.pages import forward_operations_to_parent
            forward_operations_to_parent(lambda reported: _on_event({"__op_browser_operation__": True, "data": reported}))
        except Exception:
            # Optional preview: the parent just does not follow browser pages.
            _log.debug("browser operation forwarding not installed for session %s", session_id, exc_info=True)

        wrapped = _wrap_agentic_runtime_block(tool, req, _on_event, anchor_msg_id)

        import asyncio
        from openprogram.runtime.agent.call_state import (
            _render_range_override,
        )
        loop = asyncio.new_event_loop()
        render_range_token = _render_range_override.set(render_range)
        try:
            # If parent passed its own call_id (LLM-driven path: this is
            # the LLM's tool_call_id), reuse it so the placeholder we
            # write here upserts the same row the parent wrote, and the
            # nested Agent method nodes anchor under the same
            # runtime_id the parent's build_exec_dag looks up. Without
            # this the subprocess generated ``forced_<random>`` and we
            # ended up with two placeholders for one call — the parent's
            # was empty, the subprocess's had the tree, but the UI showed
            # the parent's.
            if parent_call_id:
                call_id = parent_call_id
            else:
                import uuid as _uuid
                call_id = f"forced_{_uuid.uuid4().hex[:8]}"
            from contextlib import nullcontext
            from openprogram.runtime.agent.durable_steps import subprocess_function_execution
            binding = (
                subprocess_function_execution(
                    function_log_path, execution_id=execution_id, attempt_id=attempt_id,
                    generation=generation, call_key=parent_call_id or anchor_msg_id,
                    policy=function_policy,
                    on_admitted=None if parent_call_id else _report_function_admitted(event_queue),
                )
                if attempt_id is not None and getattr(tool, "_resumable", False)
                else nullcontext()
            )
            from openprogram.runtime.agent.questions import (
                live_workflow_questions, send_live_questions_to_parent,
            )
            if attempt_id is not None:
                send_live_questions_to_parent(
                    lambda data: event_queue.put({"__op_live_question__": True, "data": data}, block=False)
                )
            question_binding = (
                live_workflow_questions(
                    execution_id=execution_id, attempt_id=attempt_id,
                    generation=generation, producer_id=f"process:{os.getpid()}",
                ) if attempt_id is not None else nullcontext()
            )
            with binding, question_binding:
                result = loop.run_until_complete(
                    wrapped.execute(call_id, dict(kwargs or {}), None, None)
                )
        finally:
            _render_range_override.reset(render_range_token)
            try:
                loop.close()
            except Exception:
                # Teardown of a finished loop; the result is already in hand.
                _log.debug("tool subprocess event loop close failed", exc_info=True)

        try:
            text_out = "".join(
                c.text for c in (result.content or [])
                if hasattr(c, "text") and isinstance(c.text, str)
            )
        except Exception:
            text_out = ""
        # Return the id of the real top-level ``code`` node this call just
        # wrote (not a placeholder id — placeholders are no longer
        # persisted). The top-level node is the one named ``tool_name``
        # whose caller is NOT itself a code node (fn-form → caller ==
        # "ROOT"; LLM-driven → caller == llm-reply id). Nested
        # sub-functions are also code + may even share the name, but their
        # ``caller`` points at a code node, so excluding those isolates
        # the top-level invocation. Take the max-seq match in case the
        # session already holds earlier calls of the same function.
        real_id = None
        try:
            nodes = db.get_nodes(session_id) or []
            code_ids = {n.id for n in nodes if n.is_code()}
            tops = [
                n for n in nodes
                if n.is_code()
                and n.name == tool_name
                and n.caller not in code_ids
            ]
            if tops:
                real_id = max(tops, key=lambda n: n.seq).id
        except Exception:
            real_id = None
        # A completed Python invocation may still return a failed tool result
        # (for example a browser timeout). Preserve that failure across IPC.
        failed = bool(getattr(result, "is_error", False))
        payload = {"ok": not failed, "runtime_msg_id": real_id, "text": text_out}
        if failed:
            payload["error"] = text_out or "Agentic tool returned an error"
        with open(result_path, "wb") as f:
            pickle.dump(payload, f)
    except BaseException as e:  # noqa: BLE001
        from openprogram.runtime.agent.durable_steps import FunctionCompatibilityError, FunctionSuspended
        # A recoverable invocation can fail before its wrapper is entered (for
        # example, an unavailable retained dependency or runtime credential).
        # Preserve its completed steps and pending result destination.
        if isinstance(e, Exception) and attempt_id is not None:
            from openprogram.runtime.agent.durable_steps import (
                FunctionCompatibilityError, subprocess_function_execution,
            )
            from openprogram.runtime.execution.function_log import function_log_at
            recovery_key = parent_call_id or anchor_msg_id
            if function_log_path and function_log_at(function_log_path).settled_at_boundary(recovery_key):
                try:
                    with subprocess_function_execution(
                        function_log_path, execution_id=execution_id, attempt_id=attempt_id,
                        generation=generation, call_key=recovery_key, policy=function_policy,
                    ):
                        raise FunctionCompatibilityError(f"{type(e).__name__}: {e}")
                except FunctionSuspended as suspended:
                    e = suspended
        outcome = (
            {"function_suspended": True, "call_key": parent_call_id or anchor_msg_id,
             "incompatible": isinstance(e, FunctionCompatibilityError)}
            if isinstance(e, FunctionSuspended)
            else {"error": f"{type(e).__name__}: {e}"}
        )
        try:
            with open(result_path, "wb") as f:
                pickle.dump(outcome, f)
        except (OSError, pickle.PicklingError):
            # The parent then reports "subprocess died without writing result".
            _log.warning("error result not written to %s", result_path, exc_info=True)


def _report_function_admitted(event_queue):
    def report(call_key: str, policy: str) -> None:
        event_queue.put({"__op_function_admitted__": True,
                         "data": {"call_key": call_key, "policy": policy}}, block=False)
    return report


# ---------------------------------------------------------------------------
# user-input subprocess bridge (parent side) — Phase 2
# ---------------------------------------------------------------------------
#
# The child sends its question here and this process opens the durable wait,
# fenced to the attempt it spawned the child for. It emits the durable request
# for presentation, then forwards only its terminal outcome back to the child.

def _bridge_question_to_parent(
    data, answer_queue, pending_qids, lock, *,
    parent_session_id: str | None = None,
    execution_id: str | None = None,
    attempt_id: str | None = None,
    generation: int | None = None,
    producer_id: str | None = None,
) -> None:
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.waits import DurableWaitStore
        from openprogram.runtime.agent.questions import emit_question_asked
    except Exception:
        return

    qid = data.get("id")
    if not qid or not parent_session_id or not execution_id:
        return

    waits = DurableWaitStore(default_store())
    if data.get("session_id") != parent_session_id or attempt_id is None:
        wait = None
    else:
        try:
            wait = waits.open_wait(
                execution_id=execution_id, attempt_id=attempt_id, generation=generation,
                kind=str(data["kind"]), request=dict(data["request"]),
                policy_snapshot={"version": 1, "mode": "live", "producer_id": producer_id},
                expires_at=float(data["expires_at"]), wait_id=str(qid),
            )
        except Exception:
            _log.warning("question %s from a subprocess was refused", qid, exc_info=True)
            wait = None
    if wait is None:
        try:
            answer_queue.put({"id": qid, "outcome": "cancelled", "value": None}, block=False)
        except (queue.Full, ValueError):
            _log.debug("refusal of question %s not sent to child", qid, exc_info=True)
        return
    execution = default_store().get_execution(execution_id)
    if execution is None or execution.session_id != parent_session_id:
        return
    if wait.policy_snapshot.get("mode") == "live":
        from openprogram.runtime.execution.attempts import AttemptStore
        owner = AttemptStore(default_store()).get(wait.attempt_id)
        if (wait.status.value not in {"open", "claimed"}
                or execution.status.value != "running"
                or execution.current_attempt_id != wait.attempt_id
                or execution.owner_lease.get("generation") != wait.generation
                or owner is None or owner.status.value != "active"):
            return
    with lock:
        pending_qids.add(qid)

    # The outbound frame is projected from the persisted request, rather than
    # trusting mutable child envelope fields.
    forwarded = dict(wait.request)
    forwarded.update({
        "id": wait.wait_id,
        "kind": wait.kind,
        "session_id": parent_session_id,
        "execution_id": wait.execution_id,
        "wait_generation": wait.claim_generation,
        "expected_version": execution.status_version,
        "created_at": wait.created_at,
        "expires_at": wait.expires_at,
    })
    emit_question_asked(forwarded)

    def _wait_and_forward() -> None:
        res = None
        while res is None:
            try:
                current = waits.get_wait(str(qid))
            except Exception:
                current = None
            if current is None:
                break
            if current.status.value not in {"open", "claimed"}:
                outcomes = {
                    "resolved": "answered", "declined": "declined",
                    "expired": "timeout", "cancelled": "cancelled",
                }
                res = (outcomes[current.status.value], current.answer)
                break
            time.sleep(0.1)
        with lock:
            pending_qids.discard(qid)
        outcome, value = res if res is not None else ("declined", None)
        try:
            answer_queue.put({"id": qid, "outcome": outcome, "value": value},
                             block=False)
        except (queue.Full, ValueError):
            # Queue.put(block=False): Full when full, ValueError once the child
            # exited and the queue closed; the child question then times out.
            _log.debug("answer for question %s not sent to child", qid, exc_info=True)

    threading.Thread(target=_wait_and_forward, daemon=True).start()


# ---------------------------------------------------------------------------
# Parent API
# ---------------------------------------------------------------------------

def _function_process_settings(
    session_id: str, execution_id: str, attempt_id: str | None,
) -> tuple[str | None, str | None]:
    """Where a function subprocess records its steps, and which code it may use."""
    if attempt_id is None:
        return None, None
    from openprogram.runtime.agent.durable_steps import default_policy
    from openprogram.runtime.execution import default_store

    store = default_store()
    return str(store.function_log_path(session_id, execution_id)), default_policy(store, execution_id)


def _deliver_pending_pause(execution_id: str, attempt_id: str | None, generation: int | None) -> None:
    """A pause accepted while the subprocess was starting reaches it here."""
    if attempt_id is None:
        return
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.model import CommandKind, CommandStatus

    store = default_store()
    if store.list_commands(execution_id, kinds=(CommandKind.PAUSE,),
                           statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING)):
        store.function_log(execution_id).request("pause", generation)


def _pause_incompatible_function(execution_id: str, attempt_id: str) -> None:
    """The child found its saved progress cannot continue; pause the execution here."""
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.runtime.agent.durable_steps import request_incompatible_pause
    from openprogram.runtime.execution import default_control_service, default_store

    # This thread may be running under an event loop that cannot be re-entered.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(
            request_incompatible_pause, default_store(), execution_id, attempt_id,
            service=default_control_service(),
        ).result()


def _capture_sandbox_snapshot() -> dict:
    from openprogram.host.sandbox import policy_snapshot
    return policy_snapshot()

def run_agent_method_in_subprocess(
    *,
    tool_name: str,
    kwargs: dict,
    session_id: str,
    anchor_msg_id: str,
    work_dir: Optional[str] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    parent_call_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
    generation: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    authority: Optional[dict] = None,
    permission_rules_snapshot: Optional[dict] = None,
    surface_context_snapshot: Optional[dict] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    response_format=None,
    render_range: Optional[dict[str, int]] = None,
    timeout_seconds: Optional[float] = None,
    budget_seconds: Optional[float] = None,
    model_setup_error: Optional[dict] = None,
    original_owner_input=None,
    permission_mode_snapshot: Optional[str] = None,
    detached: bool = False,
) -> dict:
    """Run a single Agent method tool in a fork()'d subprocess.

    Blocks until the child exits (normally, via the optional wall-clock
    timeout, or via SIGKILL from ``kill_active_subprocess``). Returns whatever
    the child wrote to its result file, or a killed marker if it died without
    writing.

    ``timeout_seconds`` is the hard limit. ``budget_seconds`` is the budget
    the tool itself was given (see :func:`agentic_subprocess_limits`); it
    only shapes the message of a timeout, which also carries the progress
    the child reported.
    """
    result_path = tempfile.mktemp(prefix="op_subproc_", suffix=".pkl")
    # ``spawn`` (not fork) because the parent worker has already loaded
    # PyTorch/libomp + (potentially) Cocoa frameworks; fork()'ing leaves
    # libdispatch / libomp in an unsafe state and the child SIGSEGVs the
    # first time it does a BLAS call. Spawn pays a one-time ~1s import
    # cost but is rock-stable.
    ctx = mp.get_context("spawn")
    event_queue: mp.Queue = ctx.Queue()
    # parent→child answer channel (user-input-requests.md Phase 2): the
    # child blocks in runtime.ask; the parent routes the user's reply back
    # through this queue so the child's local registry can wake the call.
    answer_queue: mp.Queue = ctx.Queue()
    # parent→child graceful-stop channel: first stop click sends a sentinel
    # here. The child does not consume it; the parent escalates to SIGKILL
    # when the child doesn't exit within the grace window.
    stop_queue: mp.Queue = ctx.Queue()

    # Snapshot the parent's UsageContext so the child can restore it after
    # spawn (spawn doesn't copy contextvars). Best-effort — the child
    # operates unattributed if the metering module is unavailable.
    try:
        from openprogram.models.usage.context import snapshot as _uctx_snapshot
        usage_ctx_snapshot: Optional[dict] = _uctx_snapshot()
    except Exception:
        usage_ctx_snapshot = None
    sandbox_policy_snapshot = _capture_sandbox_snapshot()
    response_format_snapshot = (
        response_format.model_dump(mode="json")
        if hasattr(response_format, "model_dump")
        else response_format
    )

    if (attempt_id is None) != (generation is None):
        raise ValueError("attempt_id and generation must be supplied together")
    if attempt_id is not None and not execution_id:
        raise ValueError("exact execution_id is required with attempt binding")
    eid = execution_id or parent_call_id or session_id
    function_log_path, function_policy = _function_process_settings(session_id, eid, attempt_id)
    p = ctx.Process(
        target=_child_entry,
        args=(tool_name, dict(kwargs or {}), session_id, anchor_msg_id,
              work_dir, result_path, event_queue, parent_call_id,
              answer_queue, stop_queue, response_format_snapshot,
              render_range, usage_ctx_snapshot, sandbox_policy_snapshot,
              authority, permission_rules_snapshot, surface_context_snapshot,
              provider, model, eid, attempt_id, generation, model_setup_error,
              original_owner_input, permission_mode_snapshot,
              _link_registration_modules(), function_log_path, function_policy, detached),
        daemon=False,
    )
    p.start()

    with _active_lock:
        _active[eid] = p
        _active_stop_q[eid] = stop_queue
    _deliver_pending_pause(eid, attempt_id, generation)
    try:
        from openprogram.runtime.execution.run_control import register_execution_owner
        register_execution_owner(
            eid, session_id, process=p, stop_queue=stop_queue,
        )
    except Exception:
        # Run control then cannot stop this execution by id; the local
        # _active registry still can.
        _log.warning("execution owner %s not registered for session %s", eid, session_id, exc_info=True)

    # Drain events from the queue and forward to parent's on_event
    # while the child runs. Stops when the child exits + the queue
    # drains.
    webtab_finalizing = threading.Event()
    # qids this subprocess has asked about, so kill/cleanup can decline
    # them (and their parent-side waiter threads exit).
    pending_qids: set[str] = set()
    pending_qids_lock = threading.Lock()
    tracked_webtabs: dict[str, dict] = {}
    webtab_cleanup_lock = threading.Lock()
    bridge_cleanup_failures: list[dict] = []
    surface_snapshot = surface_context_snapshot or {}
    allowed_window_id = str(
        surface_snapshot.get("origin_window_id")
        or surface_snapshot.get("window_id")
        or ""
    )
    allowed_webtab_bindings = {
        str(item.get("binding_id"))
        for item in surface_snapshot.get("surfaces") or []
        if isinstance(item, dict)
        and item.get("binding_id")
        and (
            not item.get("window_id")
            or str(item.get("window_id")) == allowed_window_id
        )
    }

    progress = _ChildProgress()
    from openprogram.runtime.execution.worker_calls import WorkerCallServer
    worker_call_server = WorkerCallServer(answer_queue, p.is_alive)

    def _handle(env) -> None:
        try:
            progress.record(env)
        except Exception:
            # Progress is diagnostic; the event is still handled below.
            _log.debug("child progress not recorded for execution %s", eid, exc_info=True)
        if worker_call_server.handle_message(env):
            return
        if isinstance(env, dict) and env.get("__op_webtab__"):
            data = env.get("data") or {}
            with webtab_cleanup_lock:
                if webtab_finalizing.is_set():
                    answer_queue.put({
                        "__op_webtab_result__": True,
                        "req_id": data.get("req_id"),
                        "result": {
                            "ok": False,
                            "reason_code": "page_context_stale",
                            "error": "agent subprocess already terminated",
                        },
                    })
                    return
                result = _bridge_webtab_to_parent(
                    data,
                    answer_queue,
                    tracked_webtabs,
                    allowed_window_id=allowed_window_id,
                    allowed_bindings=allowed_webtab_bindings,
                    session_id=session_id,
                )
                if result.get("reason_code") == "page_cleanup_failed":
                    bridge_cleanup_failures.append({
                        "error": str(
                            result.get("error")
                            or "Page cleanup could not be confirmed"
                        ),
                    })
            return
        # Intercept the user-input bridge envelope: a question the child
        # raised via runtime.ask. Register it on the PARENT registry +
        # broadcast to the frontend, and arrange to route the answer back
        # through ``answer_queue``.
        if isinstance(env, dict) and env.get("__op_live_question__"):
            _bridge_question_to_parent(
                env.get("data") or {}, answer_queue,
                pending_qids, pending_qids_lock,
                parent_session_id=session_id,
                execution_id=eid,
                attempt_id=attempt_id,
                generation=generation,
                producer_id=f"process:{p.pid}",
            )
            return
        if isinstance(env, dict) and env.get("__op_function_admitted__"):
            from openprogram.runtime.agent.durable_steps import publish_function_checkpoint
            from openprogram.runtime.execution import default_store
            data = env.get("data") or {}
            publish_function_checkpoint(
                default_store(), eid, attempt_id, str(data["call_key"]), str(data["policy"]),
            )
            return
        if isinstance(env, dict) and env.get("__op_native_window__"):
            try:
                from openprogram.host.resources.native_window import (
                    record_action_point, record_control, record_operated_window,
                )
                data = env.get("data")
                if isinstance(data, dict) and data.get("kind") == "action":
                    record_action_point(data, session_id=session_id)
                elif isinstance(data, dict) and data.get("kind") == "control":
                    record_control(data, session_id=session_id)
                elif isinstance(data, dict):
                    record_operated_window(data, session_id=session_id, execution_id=eid)
            except Exception:
                # Optional preview state; the drain thread must keep running.
                _log.debug("native window report failed for execution %s", eid, exc_info=True)
            return
        if isinstance(env, dict) and env.get("__op_browser_operation__"):
            try:
                from openprogram.programs.applications.browser.pages import report_forwarded_operation
                report_forwarded_operation(env.get("data") or {}, execution_id=eid)
            except Exception:
                logging.getLogger(__name__).debug("forwarded browser operation report failed", exc_info=True)
            return
        if isinstance(env, dict) and env.get("type") == "goal_update":
            try:
                sid = str((env.get("data") or {}).get("session_id") or "")
                if sid:
                    from openprogram.runtime.context.session_db import default_db
                    default_db().invalidate_cache(sid)
            except Exception:
                # Cache refresh only; the next read reloads from disk.
                _log.debug("goal cache invalidation failed for execution %s", eid, exc_info=True)
        # execution_stream.v1: parent validates identity against the launch
        # record, then forwards. Do not reallocate revision here.
        if (
            isinstance(env, dict)
            and env.get("type") == "chat_response"
            and isinstance(env.get("data"), dict)
            and env["data"].get("type") == "execution_stream"
        ):
            try:
                from openprogram.runtime.agent.runtime.execution_stream import (
                    validate_and_forward_envelope,
                )
                validate_and_forward_envelope(
                    env,
                    session_id=session_id,
                    execution_id=eid,
                    generation=generation,
                    on_event=on_event,
                )
            except Exception:
                # Drain thread must survive a rejected or failed envelope.
                _log.warning("execution stream envelope dropped for execution %s", eid, exc_info=True)
            return
        try:
            if on_event:
                on_event(env)
        except Exception:
            # Caller callback: the drain thread must keep running.
            _log.warning("on_event callback failed for execution %s", eid, exc_info=True)

    def _drain() -> None:
        while True:
            try:
                env = event_queue.get(timeout=0.05)
            except Exception:
                if webtab_finalizing.is_set():
                    return
                continue
            if isinstance(env, dict) and env.get(_WEBTAB_DRAIN_STOP):
                return
            _handle(env)

    drain_thread = threading.Thread(target=_drain, daemon=True)
    drain_thread.start()

    timed_out = False
    stopped_at: float | None = None
    page_cleanup_failures: list[dict] = []
    try:
        timeout = None if timeout_seconds is None else max(0.1, float(timeout_seconds))
        started = time.monotonic()
        cancel_sent = False
        if cancel_event is None and timeout is None:
            # Preserve the blocking process contract for callers that do not
            # opt into canonical cancellation or a timeout.
            p.join()
        elif cancel_event is None and timeout is not None:
            # With no cooperative cancellation channel there is no need to
            # poll.  A single bounded join also avoids a busy loop for
            # Process implementations whose timed join returns immediately
            # (for example, the lightweight process doubles used by callers).
            p.join(timeout)
            if p.is_alive():
                timed_out = True
                stopped_at = time.monotonic()
                from openprogram.host._platform.processes import kill_process_tree

                if not kill_process_tree(p.pid):
                    p.kill()
                p.join(timeout=5)
        else:
            while p.is_alive():
                p.join(0.05)
                if cancel_event is not None and cancel_event.is_set() and not cancel_sent:
                    try:
                        stop_queue.put("cancel", block=False)
                    except (queue.Full, ValueError):
                        # Queue.put(block=False): Full or closed; the child is then killed on
                        # the grace deadline instead of stopping cooperatively.
                        _log.debug("cancel not sent to child for execution %s", eid, exc_info=True)
                    cancel_sent = True
                if timeout is not None and time.monotonic() - started >= timeout:
                    timed_out = True
                    stopped_at = time.monotonic()
                    from openprogram.host._platform.processes import kill_process_tree

                    if not kill_process_tree(p.pid):
                        p.kill()
                    p.join(timeout=5)
                    break
    finally:
        # No Page command may begin after this point. A command already waiting
        # for the renderer holds ``webtab_cleanup_lock``; the bounded drain wait
        # lets it publish its Page identity before exact final cleanup.
        webtab_finalizing.set()
        try:
            event_queue.put({_WEBTAB_DRAIN_STOP: True}, block=False)
        except (queue.Full, ValueError):
            # Queue.put(block=False): Full or closed; the drain thread then stops on
            # its own timeout.
            _log.debug("webtab drain stop not queued for execution %s", eid, exc_info=True)
        # Only this process's live questions die with its call stack.
        # Predeclared durable waits belong to restart recovery instead.
        if attempt_id is not None:
            from openprogram.runtime.execution import default_store
            from openprogram.runtime.execution.waits import DurableWaitStore
            from openprogram.runtime.agent.questions import retract_question
            for qid in DurableWaitStore(default_store()).cancel_live_waits(
                execution_id=eid, attempt_id=attempt_id, generation=generation,
                producer_id=f"process:{p.pid}",
            ):
                retract_question(qid)
        page_cleanup_failures = []
        # Only this lock identifies a Page command already in flight. The
        # drain thread also delivers ordinary events, so its liveness must not
        # be reported as a Page cleanup failure.
        cleanup_lock_acquired = webtab_cleanup_lock.acquire(
            timeout=_WEBTAB_FINALIZE_TIMEOUT_SECONDS,
        )
        if cleanup_lock_acquired:
            try:
                # The in-flight bridge may have reported a cleanup failure
                # while this thread was waiting for the lock.
                page_cleanup_failures.extend(bridge_cleanup_failures)
                page_cleanup_failures.extend(
                    _cleanup_bridged_webtabs(tracked_webtabs)
                )
            finally:
                webtab_cleanup_lock.release()
        else:
            page_cleanup_failures.append({
                "error": "Page cleanup was still in progress",
            })
        try:
            drain_thread.join(timeout=0.5)
        except RuntimeError:
            # Thread.join raises RuntimeError only for a thread never started.
            _log.debug("drain thread not joined for execution %s", eid, exc_info=True)
        with _active_lock:
            if _active.get(eid) is p:
                _active.pop(eid, None)
            _active_stop_q.pop(eid, None)
        try:
            from openprogram.runtime.execution.run_control import retire_execution_owner
            retire_execution_owner(eid)
        except Exception:
            # Run control keeps a stale owner entry until its own sweep.
            _log.warning("execution owner %s not retired", eid, exc_info=True)

    # Pick up the result, if any.
    out: dict
    if timed_out:
        report = progress.report(stopped_at)
        out = {
            "error": _timeout_message(tool_name, timeout, budget_seconds, report),
            "killed": True,
            "timed_out": True,
            "progress": report,
        }
    else:
        try:
            with open(result_path, "rb") as f:
                out = pickle.load(f)
        except Exception:
            out = {
                "error": "subprocess died without writing result",
                "killed": True,
            }
    try:
        os.unlink(result_path)
    except OSError:
        # Leaves a temp file behind; nothing else depends on it.
        _log.debug("result file %s not removed", result_path, exc_info=True)

    if p.exitcode is not None and p.exitcode < 0:
        # Killed by signal (negative exitcode = -signum on POSIX).
        out.setdefault("killed", True)
        out.setdefault("signal", -p.exitcode)
    if page_cleanup_failures:
        cleanup_result = _page_cleanup_failure(str(
            page_cleanup_failures[0].get("error")
            or "Page cleanup could not be confirmed"
        ))
        cleanup_result["cleanup_failures"] = page_cleanup_failures
        out.update(cleanup_result)
        out["page_cleanup_failed"] = True
        out["page_cleanup_result"] = cleanup_result
    if out.get("incompatible") and attempt_id is not None:
        _pause_incompatible_function(eid, attempt_id)
    return out


def _execution_key(session_id: str, execution_id: str) -> str:
    """Return the exact process owner key for one execution."""
    del session_id
    if not execution_id:
        raise ValueError("execution_id is required for process control")
    return execution_id


def is_subprocess_alive(
    session_id: str, *, execution_id: str,
) -> bool:
    """True if there's a live in-flight subprocess for this execution."""
    key = _execution_key(session_id, execution_id)
    with _active_lock:
        p = _active.get(key)
    return p is not None and p.is_alive()


def request_graceful_stop(
    session_id: str, *, execution_id: str,
) -> bool:
    """Ask the in-flight subprocess to stop cooperatively via its stop queue."""
    key = _execution_key(session_id, execution_id)
    with _active_lock:
        q = _active_stop_q.get(key)
        p = _active.get(key)
    if q is None or p is None or not p.is_alive():
        return False
    try:
        q.put("stop", block=False)
        return True
    except Exception:
        return False


def kill_active_subprocess(
    session_id: str, *, execution_id: str,
) -> bool:
    """SIGKILL the process group of the in-flight subprocess for this execution."""
    key = _execution_key(session_id, execution_id)
    with _active_lock:
        p = _active.pop(key, None)
        _active_stop_q.pop(key, None)
    if p is None:
        return False
    if not p.is_alive():
        return False
    # kill_process_tree handles both POSIX (killpg + SIGKILL) and
    # Windows (taskkill /F /T). Falls back to single-process kill if
    # the target wasn't started as a session leader.
    from openprogram.host._platform.processes import kill_process_tree
    if kill_process_tree(p.pid):
        return True
    try:
        p.kill()
        return True
    except Exception:
        return False
