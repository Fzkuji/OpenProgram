"""Run @agentic_function tools in an isolated subprocess so the stop
button can SIGKILL the entire process group in milliseconds without
waiting for cooperative cancel points.

Why this exists: the chat-path / forced-tool-call wrapper used to run
the tool body on the worker's own thread. Canonical execution cancellation
can mark
the session cancelled and the @agentic_function pre-invocation hook
would eventually raise CancelledError — but only at the *next* hook
point, which for a gui_agent in the middle of a vision call could be
800–1500ms away. Users compared this to Claude Code's instant stop
and asked for the same UX.

Design:
  - Parent calls ``run_agentic_in_subprocess(...)``.
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

import multiprocessing as mp
import os
import pickle
import signal
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Optional

from openprogram.agent.surface_context import (
    page_cleanup_failure as _page_cleanup_failure,
)


# execution_id → live Process. Session is a secondary index so a
# compatibility session-level lookup still finds the current owner.
_active: dict[str, mp.Process] = {}
_active_stop_q: dict[str, "mp.Queue"] = {}
_session_execution: dict[str, str] = {}
_active_lock = threading.Lock()

_BOUNDED_AGENTIC_TOOLS = {"browser_agent", "gui_agent"}
_DEFAULT_AGENTIC_TIMEOUT_SECONDS = 300.0


def agentic_subprocess_timeout_seconds(
    tool_name: str, kwargs: dict | None,
) -> float | None:
    """Resolve the whole-process deadline for GUI/browser agent calls."""
    if tool_name not in _BOUNDED_AGENTIC_TOOLS:
        return None
    raw = (kwargs or {}).get("max_seconds")
    value = _DEFAULT_AGENTIC_TIMEOUT_SECONDS if raw is None else float(raw)
    return value if value > 0 else None


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


def _desktop_window_registration(webtab, window_id: str, *, sole: bool = False):
    registered = webtab.registered_desktop_windows()
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
    webtab,
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
        return webtab.request_open_tab(command.get("url") or "", timeout=timeout)

    selected = _desktop_window_registration(webtab, window_id, sole=True)
    if selected is None:
        return {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "originating desktop window is unavailable",
        }
    owner_ws, selected_window_id, connection_revision = selected
    result = webtab.request_on_ws(owner_ws, command, timeout)
    if (
        background
        and isinstance(result, dict)
        and result.get("reason_code") == webtab.RESPONSE_TIMEOUT_REASON_CODE
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
        webtab.validated_open_ownership(result).get("created") is True
    )

    def rollback_opened_page() -> dict | None:
        if not agent_owned:
            return None
        if not tab_id:
            return _page_cleanup_failure(
                "the opened Page identity could not be safely closed"
            )
        close_result = _bounded_page_close(lambda: webtab.request_on_ws(
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
        binding_id = webtab.register_binding(
            owner_ws,
            result_window_id,
            tab_id,
            target_id,
            geometry_revision=int(result.get("geometry_revision") or 0),
            allow_background=background,
            expected_connection_revision=connection_revision,
        )
        result["binding_id"] = binding_id
        page_key = webtab.binding_page_key(binding_id)
        if page_key:
            result["page_key"] = page_key
        result.update(webtab.binding_revisions(binding_id))
    except Exception:
        if binding_id:
            webtab.release_binding(binding_id)
        rollback_failure = rollback_opened_page()
        if rollback_failure is not None:
            return rollback_failure
        raise
    return result


def _close_bridged_webtab(
    webtab, command: dict, timeout: float, tracked_pages: dict[str, dict] | None,
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
            lambda: webtab.request_close_tab(binding_id, timeout=timeout)
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
        webtab.binding_connection(owned_binding) or page.get("owner_ws")
    )
    if owner_ws is None:
        result = {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "originating desktop window is unavailable",
        }
    else:
        result = _bounded_page_close(
            lambda: webtab.request_on_ws(owner_ws, command, timeout)
        )
    if not result.get("ok"):
        page["cleanup_exhausted"] = True
        page["cleanup_error"] = str(
            result.get("error") or "Page close was rejected"
        )
    return result


def _update_tracked_webtabs(
    webtab,
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
            owner_ws = webtab.binding_connection(binding_id)
            agent_owned = (
                webtab.validated_open_ownership(result).get("created") is True
            )
            tracked_pages[binding_id] = {
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
        webtab.release_binding(key)
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
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

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

    captured = surface_context.capture_pages(source)
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
            webtab.release_binding(current)
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "Page inventory returned another desktop window",
        }

    if tracked_pages is None:
        for current in captured_bindings:
            webtab.release_binding(current)
        return {
            "ok": False,
            "reason_code": "page_context_stale",
            "error": "parent Page binding tracker is unavailable",
        }
    for item in surfaces:
        current = str(item.get("binding_id") or "")
        if not current:
            continue
        owner_ws = webtab.binding_connection(current)
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
) -> dict:
    command = data.get("command") if isinstance(data, dict) else None
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
            from openprogram.webui.ws_actions import webtab

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
                result = webtab.request_bound_tab(
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
                result = webtab.request_bound_screenshot(
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
                    webtab, command, timeout, tracked_pages,
                )
            elif op == "open":
                result = _open_bridged_webtab(
                    webtab,
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
                result = webtab._request(command, timeout)
            _update_tracked_webtabs(
                webtab,
                command,
                result,
                tracked_pages,
                allowed_bindings,
            )
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    answer_queue.put({
        "__op_webtab_result__": True,
        "req_id": data.get("req_id") if isinstance(data, dict) else None,
        "result": result,
    })
    return result


def _cleanup_bridged_webtabs(tracked_pages: dict[str, dict]) -> list[dict]:
    """Close child-created Pages and release every parent-owned binding."""
    from openprogram.webui.ws_actions import webtab

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
                    webtab.binding_connection(binding_id) or page.get("owner_ws")
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
            close_result = _bounded_page_close(lambda: webtab.request_on_ws(
                owner_ws,
                {
                    "op": "close",
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
        webtab.release_binding(binding_id)
        tracked_pages.pop(binding_id, None)
    return failures


def _permission_rules_from_snapshot(snapshot: Optional[dict]):
    if snapshot is None:
        return None
    from openprogram.agent.session_config import PermissionRules
    return PermissionRules(
        allow=list(snapshot.get("allow") or []),
        deny=list(snapshot.get("deny") or []),
        ask=list(snapshot.get("ask") or []),
    )


# ---------------------------------------------------------------------------
# Child entry point
# ---------------------------------------------------------------------------

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
) -> None:
    # Detach into our own process group so ``killpg`` from the parent
    # takes down every grandchild (browser, subprocess providers, ...).
    try:
        os.setpgrp()
    except Exception:
        pass

    # ``spawn`` starts with a fresh interpreter. Pin it to the parent's
    # effective policy before any tool/runtime is created; later config edits
    # cannot widen an already-running child.
    from openprogram.sandbox import install_policy_snapshot
    install_policy_snapshot(sandbox_policy_snapshot or {"enabled": False})
    if surface_context_snapshot is not None:
        from openprogram.agent.surface_context import bind as _bind_surface
        _bind_surface(surface_context_snapshot)

    # Restore the parent's UsageContext, then override call_kind/call_label
    # with this subprocess's actual identity. The snapshot carries the
    # parent's session_id (valuable — keeps attribution to the session that
    # triggered this tool), but the parent's call_kind is typically "chat"
    # which is wrong for an @agentic_function subprocess. Set it to "exec"
    # with the tool's name as call_label so metering can distinguish
    # research_agent / gui_agent / wiki_agent etc.
    try:
        from openprogram.usage.context import (
            apply_snapshot as _apply_uctx,
            usage_scope as _usage_scope,
            _current as _usage_cur,
            UsageContext,
        )
        _apply_uctx(usage_ctx_snapshot)
        from openprogram.usage.context import current_usage_context
        _parent = current_usage_context()
        _usage_cur.set(UsageContext(
            call_kind="exec",
            call_label=tool_name,
            session_id=_parent.session_id or session_id,
            parent_session_id=_parent.parent_session_id,
            agent_id=_parent.agent_id,
        ))
    except Exception:
        pass

    # --- graceful-stop bridge (child side) ---
    # The parent's FIRST stop click sends a sentinel down ``stop_queue``.
    # We flip a process-local Event that long-running harness loops poll
    # (research_harness.stop) so they finish the in-flight unit and return
    # cleanly — instead of being SIGKILLed mid-step. The parent escalates to
    # SIGKILL if the child doesn't exit within a grace window (2nd click /
    # timeout). spawn means this child is a fresh interpreter, so we install
    # a brand-new Event here.
    if stop_queue is not None:
        try:
            import threading as _threading

            _stop_ev = _threading.Event()

            def _install_into_harness() -> None:
                # Best-effort: research_harness may not be importable in every
                # subprocess (e.g. a non-research tool). Harmless if absent.
                try:
                    from research_harness import stop as _hstop
                    _hstop.install_stop_event(_stop_ev)
                except Exception:
                    pass

            _install_into_harness()

            def _stop_pump() -> None:
                while True:
                    try:
                        msg = stop_queue.get()
                    except Exception:
                        return
                    if msg is None:
                        return
                    # Any message = graceful stop requested.
                    _stop_ev.set()
                    _install_into_harness()  # in case import happened after start
                    return

            _threading.Thread(target=_stop_pump, daemon=True).start()
        except Exception:
            pass

    # --- durable user-input subprocess bridge: answer side ---
    # The parent only forwards a terminal durable-wait projection.  It never
    # accepts or resolves a question locally; the child wakes its local thread
    # and reads the canonical row itself.
    def handle_webtab_answer(_message):
        return False

    if answer_queue is not None:
        try:
            from openprogram.agent.questions import get_question_registry
            from openprogram.webui.ws_actions import webtab

            webtab._request, handle_webtab_answer = _new_child_webtab_bridge(
                event_queue
            )

            def _answer_pump() -> None:
                reg = get_question_registry()
                while True:
                    try:
                        msg = answer_queue.get()
                    except Exception:
                        return
                    if msg is None:  # shutdown sentinel
                        return
                    if handle_webtab_answer(msg):
                        continue
                    try:
                        qid = msg.get("id")
                        if qid:
                            reg.wake(qid)
                    except Exception:
                        pass

            threading.Thread(target=_answer_pump, daemon=True).start()
        except Exception:
            pass
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
        pass

    # Re-install the session-scoped ContextVars. fork inherits the
    # snapshot, but we set them explicitly anyway so a spawn fallback
    # would still work.
    try:
        from openprogram.store import (
            SessionNodeWriter,
            _store as _store_var,
            _current_turn_id as _turn_id_var,
        )
        from openprogram.agentic_programming.function import (
            _current_runtime as _current_runtime_var,
        )
        from openprogram.agent.session_db import default_db
        from openprogram.providers.registry import create_runtime
        from openprogram.programs._runtime import get as _get_tool
        from openprogram.agent.dispatcher import (
            _wrap_agentic_runtime_block,
            TurnRequest,
        )
        from openprogram.agent.run_control import (
            set_current_session_id as _set_cid,
            set_current_execution_id as _set_eid,
        )

        # Drop any inherited DB handle and re-acquire so we don't share
        # a sqlite connection with the parent (sqlite handles after fork
        # are unsafe).
        try:
            import openprogram.agent.session_db as _sdb_mod
            for attr in ("_default_db", "_DB_SINGLETON", "_db"):
                if hasattr(_sdb_mod, attr):
                    setattr(_sdb_mod, attr, None)
        except Exception:
            pass

        db = default_db()
        _store_var.set(SessionNodeWriter(db, session_id))
        _turn_id_var.set(anchor_msg_id)
        _set_cid(session_id)
        # spawn does not copy ContextVars; restore the process owner's exact
        # id so child-created questions carry the same cancellation owner.
        _set_eid(execution_id or parent_call_id or session_id)

        rt = create_runtime(provider=provider, model=model)
        if response_format_snapshot is not None:
            from openprogram.agentic_programming.runtime import _current_response_format
            from openprogram.providers.structured_output import normalize_response_format
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
                from openprogram.agent.questions import QueueTransport
                rt.set_question_transport(QueueTransport(event_queue))
            except Exception:
                pass
        if work_dir:
            try:
                abs_wd = os.path.abspath(os.path.expanduser(work_dir))
                os.makedirs(abs_wd, exist_ok=True)
                from openprogram.worktree.context import set_worktree
                set_worktree(abs_wd)
                if hasattr(rt, "set_workdir"):
                    rt.set_workdir(abs_wd)
            except Exception:
                pass
        _current_runtime_var.set(rt)

        tool = _get_tool(tool_name)
        if tool is None:
            with open(result_path, "wb") as f:
                pickle.dump({"error": f"tool not found: {tool_name}"}, f)
            return

        req = TurnRequest(
            session_id=session_id,
            user_text="",
            agent_id="main",
            source="web",
            render_range=render_range,
            permission_rules=_permission_rules_from_snapshot(
                permission_rules_snapshot
            ),
            **(authority_snapshot or {}),
        )
        # Same context the dispatcher binds in-process: an inner
        # AgentSession created inside this tool inherits it.
        from openprogram.agent.turn_request_context import set_turn_request
        set_turn_request(req)

        # Bridge child-side on_event into the parent via the queue.
        def _on_event(env: dict) -> None:
            try:
                event_queue.put(env, block=False)
            except Exception:
                pass

        # Agentic functions run in a spawned process whose EventBus has no
        # Web subscriber. Forward the existing typed goal.update event through
        # the same parent event queue used by stream updates; this keeps Goal
        # state live without adding a Goal-specific transport.
        try:
            from openprogram.events import get_event_bus

            def _forward_goal_update(event) -> None:
                payload = dict(getattr(event, "payload", None) or {})
                _on_event({"type": "goal_update", "data": payload})

            get_event_bus().subscribe(
                _forward_goal_update,
                types={"goal.update"},
            )
        except Exception:
            pass

        wrapped = _wrap_agentic_runtime_block(tool, req, _on_event, anchor_msg_id)

        import asyncio
        from openprogram.agentic_programming.function import (
            _render_range_override,
        )
        loop = asyncio.new_event_loop()
        render_range_token = _render_range_override.set(render_range)
        try:
            # If parent passed its own call_id (LLM-driven path: this is
            # the LLM's tool_call_id), reuse it so the placeholder we
            # write here upserts the same row the parent wrote, and the
            # nested @agentic_function nodes anchor under the same
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
            result = loop.run_until_complete(
                wrapped.execute(call_id, dict(kwargs or {}), None, None)
            )
        finally:
            _render_range_override.reset(render_range_token)
            try:
                loop.close()
            except Exception:
                pass

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
        with open(result_path, "wb") as f:
            pickle.dump(
                {"ok": True, "runtime_msg_id": real_id, "text": text_out},
                f,
            )
    except BaseException as e:  # noqa: BLE001
        try:
            with open(result_path, "wb") as f:
                pickle.dump(
                    {"error": f"{type(e).__name__}: {e}"}, f,
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# user-input subprocess bridge (parent side) — Phase 2
# ---------------------------------------------------------------------------
#
# The child commits a durable wait before it sends this envelope.  On the
# parent side we verify the child cannot choose another owner, emit the durable
# request for presentation, then forward only its terminal projection back to
# the child.  SQLite is the sole lifecycle authority across the two processes.

def _bridge_question_to_parent(
    data, answer_queue, pending_qids, lock, *,
    parent_session_id: str | None = None,
    execution_id: str | None = None,
) -> None:
    try:
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore
        from openprogram.agent.questions import emit_question_asked
    except Exception:
        return

    qid = data.get("id")
    if not qid or not parent_session_id or not execution_id:
        return

    waits = DurableWaitStore(default_store())
    wait = waits.get_wait(str(qid))
    if wait is None or wait.execution_id != execution_id:
        return
    execution = default_store().get_execution(execution_id)
    if execution is None or execution.session_id != parent_session_id:
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
        except Exception:
            pass

    threading.Thread(target=_wait_and_forward, daemon=True).start()


# ---------------------------------------------------------------------------
# Parent API
# ---------------------------------------------------------------------------

def _capture_sandbox_snapshot() -> dict:
    from openprogram.sandbox import policy_snapshot
    return policy_snapshot()

def run_agentic_in_subprocess(
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
) -> dict:
    """Run a single @agentic_function tool in a fork()'d subprocess.

    Blocks until the child exits (normally, via the optional wall-clock
    timeout, or via SIGKILL from ``kill_active_subprocess``). Returns whatever
    the child wrote to its result file, or a killed marker if it died without
    writing.
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
    # here; the child flips its harness stop flag and finishes the in-flight
    # unit. The parent escalates to SIGKILL only if the child doesn't exit.
    stop_queue: mp.Queue = ctx.Queue()

    # Snapshot the parent's UsageContext so the child can restore it after
    # spawn (spawn doesn't copy contextvars). Best-effort — the child
    # operates unattributed if the metering module is unavailable.
    try:
        from openprogram.usage.context import snapshot as _uctx_snapshot
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
    p = ctx.Process(
        target=_child_entry,
        args=(tool_name, dict(kwargs or {}), session_id, anchor_msg_id,
              work_dir, result_path, event_queue, parent_call_id,
              answer_queue, stop_queue, response_format_snapshot,
              render_range, usage_ctx_snapshot, sandbox_policy_snapshot,
              authority, permission_rules_snapshot, surface_context_snapshot,
              provider, model, eid),
        daemon=False,
    )
    p.start()

    with _active_lock:
        _active[eid] = p
        _active_stop_q[eid] = stop_queue
        _session_execution[session_id] = eid
    try:
        from openprogram.agent.run_control import register_execution_owner
        register_execution_owner(
            eid, session_id, process=p, stop_queue=stop_queue,
        )
    except Exception:
        pass

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

    def _handle(env) -> None:
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
        if isinstance(env, dict) and env.get("__op_question__"):
            _bridge_question_to_parent(
                env.get("data") or {}, answer_queue,
                pending_qids, pending_qids_lock,
                parent_session_id=session_id,
                execution_id=eid,
            )
            return
        if isinstance(env, dict) and env.get("type") == "goal_update":
            try:
                sid = str((env.get("data") or {}).get("session_id") or "")
                if sid:
                    from openprogram.agent.session_db import default_db
                    default_db().invalidate_cache(sid)
            except Exception:
                pass
        try:
            if on_event:
                on_event(env)
        except Exception:
            pass

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
                from openprogram._compat import kill_process_tree

                if not kill_process_tree(p.pid):
                    p.kill()
                p.join(timeout=5)
        else:
            while p.is_alive():
                p.join(0.05)
                if cancel_event is not None and cancel_event.is_set() and not cancel_sent:
                    try:
                        stop_queue.put("cancel", block=False)
                    except Exception:
                        pass
                    cancel_sent = True
                if timeout is not None and time.monotonic() - started >= timeout:
                    timed_out = True
                    from openprogram._compat import kill_process_tree

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
        except Exception:
            pass
        # The child may have exited while a durable wait remains open.  Do not
        # invent a decline here: cancellation and expiry are canonical control
        # transitions, and restart recovery may later resume this execution.
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
        except Exception:
            pass
        with _active_lock:
            if _active.get(eid) is p:
                _active.pop(eid, None)
            _active_stop_q.pop(eid, None)
            if _session_execution.get(session_id) == eid:
                _session_execution.pop(session_id, None)
        try:
            from openprogram.agent.run_control import retire_execution_owner
            retire_execution_owner(eid)
        except Exception:
            pass

    # Pick up the result, if any.
    out: dict
    if timed_out:
        out = {
            "error": f"agentic subprocess timed out after {timeout:g} seconds",
            "killed": True,
            "timed_out": True,
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
    except Exception:
        pass

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
    return out


def _execution_key(session_id: str, execution_id: str | None = None) -> str:
    if execution_id:
        return execution_id
    with _active_lock:
        return _session_execution.get(session_id) or session_id


def is_subprocess_alive(
    session_id: str, *, execution_id: str | None = None,
) -> bool:
    """True if there's a live in-flight subprocess for this execution."""
    key = _execution_key(session_id, execution_id)
    with _active_lock:
        p = _active.get(key)
        if p is None and execution_id is None:
            p = _active.get(session_id)
    return p is not None and p.is_alive()


def request_graceful_stop(
    session_id: str, *, execution_id: str | None = None,
) -> bool:
    """Ask the in-flight subprocess to stop cooperatively via its stop queue."""
    key = _execution_key(session_id, execution_id)
    with _active_lock:
        q = _active_stop_q.get(key)
        p = _active.get(key)
        if q is None and execution_id is None:
            q = _active_stop_q.get(session_id)
            p = _active.get(session_id)
    if q is None or p is None or not p.is_alive():
        return False
    try:
        q.put("stop", block=False)
        return True
    except Exception:
        return False


def kill_active_subprocess(
    session_id: str, *, execution_id: str | None = None,
) -> bool:
    """SIGKILL the process group of the in-flight subprocess for this execution."""
    key = _execution_key(session_id, execution_id)
    with _active_lock:
        p = _active.pop(key, None)
        _active_stop_q.pop(key, None)
        if p is None and execution_id is None:
            p = _active.pop(session_id, None)
            _active_stop_q.pop(session_id, None)
        if _session_execution.get(session_id) == key:
            _session_execution.pop(session_id, None)
    if p is None:
        return False
    if not p.is_alive():
        return False
    # kill_process_tree handles both POSIX (killpg + SIGKILL) and
    # Windows (taskkill /F /T). Falls back to single-process kill if
    # the target wasn't started as a session leader.
    from openprogram._compat import kill_process_tree
    if kill_process_tree(p.pid):
        return True
    try:
        p.kill()
        return True
    except Exception:
        return False
