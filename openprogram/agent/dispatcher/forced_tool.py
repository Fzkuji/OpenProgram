"""Forced tool-call dispatch — run a single @agentic_function without
invoking the LLM.

Extracted from dispatcher/__init__.py (dispatcher-split step 2). This is
a leaf: it shares the runtime-block placeholder/finalize plumbing with an
LLM-issued tool call, but pulls everything it needs through in-function
local imports, so it depends only on the stdlib + ``types`` here. The
package ``__init__`` re-exports ``dispatch_forced_tool_call`` so
``from openprogram.agent.dispatcher import dispatch_forced_tool_call``
(webui/routes/chat.py) resolves unchanged.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from openprogram.agent.dispatcher.types import EventCallback, _noop

_log = logging.getLogger(__name__)


def _execution_id_from_anchor(anchor_msg_id: str | None) -> str | None:
    if not isinstance(anchor_msg_id, str):
        return None
    marker = "|node:"
    if marker not in anchor_msg_id:
        return None
    node_id = anchor_msg_id.rsplit(marker, 1)[-1].strip()
    return node_id or None


def dispatch_forced_tool_call(
    session_id: str,
    anchor_msg_id: str,
    tool_name: str,
    tool_input: dict | None,
    work_dir: Optional[str] = None,
    *,
    agent_id: str = "main",
    source: str = "web",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    response_format=None,
    on_event: Optional[EventCallback] = None,
    execution_id: Optional[str] = None,
) -> dict:
    """Run a single @agentic_function without invoking the LLM.

    Shares the exact same wrapper / placeholder / finalize plumbing as
    an LLM-issued tool call (see ``_wrap_agentic_runtime_block``).
    Used by the Functions panel / fn-form / former ``/run`` UI path,
    so all @agentic_function invocations land on one execution path.

    Caller is responsible for having already persisted the user-side
    command message under ``anchor_msg_id`` — this function only adds
    the runtime-block row + the DAG subtree.
    """
    on_event = on_event or _noop

    # Look up by registry name so user-only tools (expose=False) can still
    # be started from Programs / fn-form. Agent tool tables stay filtered.
    try:
        from openprogram.programs._runtime import get as _get_tool
        tool = _get_tool(tool_name)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"failed to resolve tool {tool_name!r}: {e}") from e
    if tool is None:
        # The welcome screen advertises the bundled programs whether or
        # not they are installed — a catalogued-but-missing one gets an
        # actionable message (the GUI agent is opt-in: it pulls PyTorch).
        try:
            from openprogram.programs._programs import get_program
            prog = get_program(tool_name)
        except Exception:
            prog = None
        if prog is not None and not prog.is_installed():
            size = (" — it downloads PyTorch (~300 MB; ~3 GB on CUDA)"
                    if prog.heavy else "")
            raise ValueError(
                f"{prog.function} is not installed{size}. Install it with: "
                f"openprogram programs install {prog.extra}  (or via "
                f"`openprogram setup` → programs), then restart."
            )
        raise ValueError(f"tool not found: {tool_name!r}")
    if not getattr(tool, "_is_agentic", False):
        raise ValueError(
            f"tool {tool_name!r} is not an @agentic_function — only "
            "agentic tools can be forced via this path"
        )

    # New path: forked subprocess so handle_stop can SIGKILL the
    # entire process group in milliseconds. The child re-installs the
    # session ContextVars and re-wraps the tool with
    # _wrap_agentic_runtime_block; events are bridged back via an
    # mp.Queue so WS clients see the same envelopes as before.
    from openprogram.agent.process_runner import (
        agentic_subprocess_timeout_seconds,
        run_agentic_in_subprocess,
    )
    from openprogram.agent.run_control import (
        set_current_session_id as _set_cid,
        reset_current_session_id as _reset_cid,
    )
    _cid_token = _set_cid(session_id)
    captured_surface = None
    out = None
    try:
        resolved_execution_id = (
            execution_id or _execution_id_from_anchor(anchor_msg_id)
        )
        browser_surface = (
            tool_name == "browser_agent"
            or (
                tool_name == "gui_agent"
                and (
                    str((tool_input or {}).get("surface") or "desktop")
                    .strip()
                    .lower()
                    == "browser"
                    or bool((tool_input or {}).get("backend"))
                )
            )
        )
        surface_snapshot = None
        if browser_surface:
            from openprogram.agent import surface_context

            captured_surface = surface_context.capture_pages()
            surface_snapshot = captured_surface
        out = run_agentic_in_subprocess(
            tool_name=tool_name,
            kwargs=dict(tool_input or {}),
            session_id=session_id,
            anchor_msg_id=anchor_msg_id,
            work_dir=work_dir,
            on_event=on_event,
            execution_id=resolved_execution_id,
            provider=provider,
            model=model,
            response_format=response_format,
            timeout_seconds=agentic_subprocess_timeout_seconds(
                tool_name, tool_input,
            ),
            surface_context_snapshot=surface_snapshot,
        )
    finally:
        if captured_surface is not None:
            from openprogram.agent.surface_context import release_bindings

            release_bindings(captured_surface)
        try:
            _reset_cid(_cid_token)
        except ValueError:
            _log.debug("call-id contextvar reset in foreign context",
                       exc_info=True)
        # Subprocess wrote every nested Call directly to the per-session
        # git history via its OWN SessionStore. Parent worker's cached
        # SessionMemoryIndex never observed those writes — drop the
        # cache so handle_load_session / build_branches_payload read
        # the on-disk truth instead of the pre-subprocess snapshot
        # (which contains only the user msg + runtime placeholder).
        try:
            from openprogram.agent.session_db import default_db as _ddb
            _ddb().invalidate_cache(session_id)
        except Exception:
            # A stale cache shows the pre-subprocess snapshot until the next
            # write; recoverable, but worth a breadcrumb.
            _log.debug("session cache invalidation failed for %s",
                       session_id, exc_info=True)
        # fn-form / direct-run is a standalone call — the user msg + the
        # top-level code node ARE the main branch. Without advancing
        # head_id to that code node, HEAD stays pinned to the user msg
        # and the conv reads as ``detached`` (HEAD ≠ conv tip). The
        # LLM-called path advances head_id in process_user_turn step 6;
        # the forced path was missing the equivalent step. ``runtime_msg_id``
        # is the real persisted code-node id (or None if it couldn't be
        # located — in which case we leave HEAD alone rather than point
        # it at a dangling id).
        _rt_id = (out or {}).get("runtime_msg_id")
        if _rt_id:
            try:
                from openprogram.agent.session_db import default_db as _ddb
                _ddb().update_session(session_id, head_id=_rt_id)
            except Exception:
                _log.warning("failed to advance head for session %s",
                             session_id, exc_info=True)

    _terminal_status = "interrupted"
    if resolved_execution_id:
        from openprogram.agent.run_control import mark_execution_terminal
        if out.get("timed_out") or out.get("error"):
            _terminal_status = "error"
        elif out.get("killed"):
            _cancel_intent = False
            try:
                from openprogram.agent.session_db import default_db as _ddb
                _record = next(
                    (
                        node for node in _ddb().get_nodes(session_id)
                        if node.id == resolved_execution_id
                    ),
                    None,
                )
                _meta = (_record.metadata or {}) if _record is not None else {}
                _cancel_intent = bool(
                    _meta.get("cancellation_requested_at")
                    or _meta.get("status") in {"cancelling", "cancelled"}
                )
            except Exception:
                _log.debug(
                    "failed to read cancellation intent for %s",
                    resolved_execution_id,
                    exc_info=True,
                )
            _terminal_status = (
                "cancelled" if _cancel_intent else "interrupted"
            )
        else:
            _terminal_status = "completed"
        mark_execution_terminal(resolved_execution_id, _terminal_status)

    if out.get("killed") and not out.get("timed_out"):
        # If the subprocess was SIGKILLed before it could finalize the
        # runtime-block, patch the placeholder so the UI doesn't show
        # a stuck spinner. handle_stop also patches running rows, so
        # this is a belt-and-suspenders cleanup.
        try:
            from openprogram.agent.session_db import default_db as _ddb
            from openprogram.store import SessionNodeWriter as _GS
            _db = _ddb()
            _shim = _GS(_db, session_id)
            for _m in (_db.get_messages(session_id) or []):
                if (_m.get("status") or "done") == "running":
                    _shim.update(
                        _m["id"],
                        metadata={
                            "status": _terminal_status,
                            "last_update_at": time.time(),
                            **(
                                {"_cancelled_reason": "user_stop"}
                                if _terminal_status == "cancelled" else {}
                            ),
                        },
                    )
        except Exception:
            # Rows left at "running" never clear in the UI — log loudly.
            _log.warning(
                "failed to mark running rows cancelled for session %s",
                session_id, exc_info=True)
        return {
            "runtime_msg_id": None,
            "ok": False,
            "killed": True,
        }
    if out.get("error"):
        # The child errored — possibly BEFORE its wrapper's finally could
        # flip the node's status (spawn crash, kwargs pickle error, tool
        # not found). If the parent pre-created the top-level card (see
        # run_agentic_function_call), it is stuck at "running"; without a
        # terminal flip the UI spins forever. Patch any leftover running
        # row to "error" so the card resolves. In-process runs (no
        # pre-create, wrapper always finalizes) have no running rows here,
        # so this is a no-op for them.
        try:
            from openprogram.agent.session_db import default_db as _ddb
            from openprogram.store import SessionNodeWriter as _GS
            _db = _ddb()
            _db.invalidate_cache(session_id)
            _shim = _GS(_db, session_id)
            for _m in (_db.get_messages(session_id) or []):
                if (
                    (_m.get("status") or "done") == "running"
                    or _m.get("id") == resolved_execution_id
                ):
                    _shim.update(
                        _m["id"],
                        output={"error": out["error"]},
                        metadata={
                            "status": "error",
                            "error": out["error"],
                            "last_update_at": time.time(),
                        },
                    )
        except Exception:
            _log.warning(
                "failed to mark running rows errored for session %s",
                session_id, exc_info=True)
        return {"runtime_msg_id": None, "ok": False, "error": out["error"]}
    return {
        "runtime_msg_id": out.get("runtime_msg_id"),
        "ok": True,
    }
