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
from typing import Optional
import threading

from openprogram.agent.dispatcher.types import EventCallback, _noop

_log = logging.getLogger(__name__)


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
    attempt_id: Optional[str] = None,
    generation: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
    surface_context_snapshot: Optional[dict] = None,
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
    if (attempt_id is None) != (generation is None):
        raise ValueError("attempt_id and generation must be supplied together")
    if attempt_id is not None and not execution_id:
        raise ValueError("exact execution_id is required with attempt binding")
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

    # New path: forked subprocess so canonical execution cancellation can
    # SIGKILL the
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
        # The canonical execution identity is supplied by AgentDriver. The
        # DAG anchor is content provenance only and can never own lifecycle.
        resolved_execution_id = execution_id
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
        surface_snapshot = surface_context_snapshot
        if browser_surface and surface_snapshot is None:
            from openprogram.agent import surface_context

            try:
                captured_surface = surface_context.capture_pages()
            except RuntimeError:
                captured_surface = surface_context.window_context()
            surface_snapshot = captured_surface
        out = run_agentic_in_subprocess(
            tool_name=tool_name,
            kwargs=dict(tool_input or {}),
            session_id=session_id,
            anchor_msg_id=anchor_msg_id,
            work_dir=work_dir,
            on_event=on_event,
            execution_id=resolved_execution_id,
            attempt_id=attempt_id,
            generation=generation,
            cancel_event=cancel_event,
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

    # Canonical AgentDriver is the only owner allowed to finish the durable
    # execution. This leaf reports subprocess facts only; it never writes a
    # terminal state or patches lifecycle rows itself.
    if out.get("page_cleanup_failed"):
        return {
            "runtime_msg_id": resolved_execution_id or out.get("runtime_msg_id"),
            "ok": False,
            "error": out.get("error") or "page cleanup failed",
            "page_cleanup_result": out.get("page_cleanup_result"),
        }
    if out.get("killed") or out.get("error"):
        return {
            "runtime_msg_id": out.get("runtime_msg_id"),
            "ok": False,
            **{key: out[key] for key in ("error", "killed", "timed_out") if key in out},
        }
    return {
        "runtime_msg_id": out.get("runtime_msg_id"),
        "ok": True,
    }
