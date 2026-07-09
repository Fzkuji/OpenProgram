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

import time
from typing import Optional

from openprogram.agent.dispatcher.types import EventCallback, _noop


def dispatch_forced_tool_call(
    session_id: str,
    anchor_msg_id: str,
    tool_name: str,
    tool_input: dict | None,
    work_dir: Optional[str] = None,
    *,
    agent_id: str = "main",
    source: str = "web",
    on_event: Optional[EventCallback] = None,
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

    # Look up the tool by name from the global catalog.
    try:
        from openprogram.functions import agent_tools as _agent_tools
        tools = _agent_tools(names=[tool_name]) or []
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"failed to resolve tool {tool_name!r}: {e}") from e
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        # The welcome screen advertises the bundled programs whether or
        # not they are installed — a catalogued-but-missing one gets an
        # actionable message (the GUI agent is opt-in: it pulls PyTorch).
        try:
            from openprogram.functions._programs import get_program
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
    from openprogram.agent.process_runner import run_agentic_in_subprocess
    from openprogram.webui._pause_stop import (
        set_current_session_id as _set_cid,
        reset_current_session_id as _reset_cid,
        clear_cancel as _clear_cancel,
    )
    _cid_token = _set_cid(session_id)
    try:
        out = run_agentic_in_subprocess(
            tool_name=tool_name,
            kwargs=dict(tool_input or {}),
            session_id=session_id,
            anchor_msg_id=anchor_msg_id,
            work_dir=work_dir,
            on_event=on_event,
        )
    finally:
        try:
            _reset_cid(_cid_token)
        except Exception:
            pass
        try:
            _clear_cancel(session_id)
        except Exception:
            pass
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
            pass
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
                pass

    if out.get("killed"):
        # If the subprocess was SIGKILLed before it could finalize the
        # runtime-block, patch the placeholder so the UI doesn't show
        # a stuck spinner. handle_stop also patches running rows, so
        # this is a belt-and-suspenders cleanup.
        try:
            from openprogram.agent.session_db import default_db as _ddb
            from openprogram.store import GraphStoreShim as _GS
            _db = _ddb()
            _shim = _GS(_db, session_id)
            for _m in (_db.get_messages(session_id) or []):
                if (_m.get("status") or "done") == "running":
                    _shim.update(
                        _m["id"],
                        metadata={
                            "status": "cancelled",
                            "last_update_at": time.time(),
                            "_cancelled_reason": "user_stop",
                        },
                    )
        except Exception:
            pass
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
            from openprogram.store import GraphStoreShim as _GS
            _db = _ddb()
            _db.invalidate_cache(session_id)
            _shim = _GS(_db, session_id)
            for _m in (_db.get_messages(session_id) or []):
                if (_m.get("status") or "done") == "running":
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
            pass
        return {"runtime_msg_id": None, "ok": False, "error": out["error"]}
    return {
        "runtime_msg_id": out.get("runtime_msg_id"),
        "ok": True,
    }
