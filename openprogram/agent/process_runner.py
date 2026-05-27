"""Run @agentic_function tools in an isolated subprocess so the stop
button can SIGKILL the entire process group in milliseconds without
waiting for cooperative cancel points.

Why this exists: the chat-path / forced-tool-call wrapper used to run
the tool body on the worker's own thread. ``handle_stop`` could mark
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
from typing import Any, Callable, Optional


# session_id → live Process. We only ever keep one in-flight forced /
# agentic subprocess per session at a time (matches the existing
# single-turn-per-session contract).
_active: dict[str, mp.Process] = {}
_active_lock = threading.Lock()


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
) -> None:
    # Detach into our own process group so ``killpg`` from the parent
    # takes down every grandchild (browser, subprocess providers, ...).
    try:
        os.setpgrp()
    except Exception:
        pass
    # Marker so the wrapper inside the child uses orig_execute directly
    # instead of recursing into another subprocess.
    os.environ["OPENPROGRAM_IN_AGENTIC_SUBPROCESS"] = "1"
    # Spawn context: re-import openprogram so the agent_tools registry
    # populates in this fresh interpreter.
    try:
        import openprogram  # noqa: F401
        import openprogram.functions  # noqa: F401
        from openprogram.functions import agent_tools as _warm
        _warm()  # force registration
    except Exception:
        pass

    # Re-install the session-scoped ContextVars. fork inherits the
    # snapshot, but we set them explicitly anyway so a spawn fallback
    # would still work.
    try:
        from openprogram.store import (
            GraphStoreShim,
            _store as _store_var,
            _current_turn_id as _turn_id_var,
        )
        from openprogram.agentic_programming.function import (
            _current_runtime as _current_runtime_var,
        )
        from openprogram.agent.session_db import default_db
        from openprogram.providers.registry import create_runtime
        from openprogram.functions import agent_tools
        from openprogram.agent.dispatcher import (
            _wrap_agentic_runtime_block,
            TurnRequest,
        )
        from openprogram.webui._pause_stop import (
            set_current_session_id as _set_cid,
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
        _store_var.set(GraphStoreShim(db, session_id))
        _turn_id_var.set(anchor_msg_id)
        _set_cid(session_id)

        rt = create_runtime()
        if work_dir:
            try:
                abs_wd = os.path.abspath(os.path.expanduser(work_dir))
                os.makedirs(abs_wd, exist_ok=True)
                if hasattr(rt, "set_workdir"):
                    rt.set_workdir(abs_wd)
            except Exception:
                pass
        _current_runtime_var.set(rt)

        tool = next(
            (t for t in (agent_tools(names=[tool_name]) or [])
             if t.name == tool_name),
            None,
        )
        if tool is None:
            with open(result_path, "wb") as f:
                pickle.dump({"error": f"tool not found: {tool_name}"}, f)
            return

        req = TurnRequest(
            session_id=session_id,
            user_text="",
            agent_id="main",
            source="web",
        )

        # Bridge child-side on_event into the parent via the queue.
        def _on_event(env: dict) -> None:
            try:
                event_queue.put(env, block=False)
            except Exception:
                pass

        wrapped = _wrap_agentic_runtime_block(tool, req, _on_event, anchor_msg_id)

        import asyncio
        loop = asyncio.new_event_loop()
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
        with open(result_path, "wb") as f:
            pickle.dump(
                {"ok": True, "runtime_msg_id": f"{anchor_msg_id}_rt_{call_id}",
                 "text": text_out},
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
# Parent API
# ---------------------------------------------------------------------------

def run_agentic_in_subprocess(
    *,
    tool_name: str,
    kwargs: dict,
    session_id: str,
    anchor_msg_id: str,
    work_dir: Optional[str] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    parent_call_id: Optional[str] = None,
) -> dict:
    """Run a single @agentic_function tool in a fork()'d subprocess.

    Blocks until the child exits (normally or via SIGKILL from
    ``kill_active_subprocess``). Returns whatever the child wrote to its
    result file, or a killed marker if it died without writing.
    """
    result_path = tempfile.mktemp(prefix="op_subproc_", suffix=".pkl")
    # ``spawn`` (not fork) because the parent worker has already loaded
    # PyTorch/libomp + (potentially) Cocoa frameworks; fork()'ing leaves
    # libdispatch / libomp in an unsafe state and the child SIGSEGVs the
    # first time it does a BLAS call. Spawn pays a one-time ~1s import
    # cost but is rock-stable.
    ctx = mp.get_context("spawn")
    event_queue: mp.Queue = ctx.Queue()
    p = ctx.Process(
        target=_child_entry,
        args=(tool_name, dict(kwargs or {}), session_id, anchor_msg_id,
              work_dir, result_path, event_queue, parent_call_id),
        daemon=False,
    )
    p.start()

    with _active_lock:
        # If a prior subprocess is somehow still tracked, replace it.
        _active[session_id] = p

    # Drain events from the queue and forward to parent's on_event
    # while the child runs. Stops when the child exits + the queue
    # drains.
    stop_flag = threading.Event()

    def _drain() -> None:
        while not stop_flag.is_set():
            try:
                env = event_queue.get(timeout=0.05)
            except Exception:
                if not p.is_alive():
                    # Drain any remaining items, then exit.
                    while True:
                        try:
                            env2 = event_queue.get_nowait()
                        except Exception:
                            return
                        try:
                            if on_event:
                                on_event(env2)
                        except Exception:
                            pass
                continue
            try:
                if on_event:
                    on_event(env)
            except Exception:
                pass

    drain_thread = threading.Thread(target=_drain, daemon=True)
    drain_thread.start()

    try:
        p.join()
    finally:
        stop_flag.set()
        try:
            drain_thread.join(timeout=0.5)
        except Exception:
            pass
        with _active_lock:
            if _active.get(session_id) is p:
                _active.pop(session_id, None)

    # Pick up the result, if any.
    out: dict
    try:
        with open(result_path, "rb") as f:
            out = pickle.load(f)
    except Exception:
        out = {"error": "subprocess died without writing result", "killed": True}
    try:
        os.unlink(result_path)
    except Exception:
        pass

    if p.exitcode is not None and p.exitcode < 0:
        # Killed by signal (negative exitcode = -signum on POSIX).
        out.setdefault("killed", True)
        out.setdefault("signal", -p.exitcode)
    return out


def kill_active_subprocess(session_id: str) -> bool:
    """SIGKILL the entire process group of the in-flight subprocess for
    ``session_id``. Returns True if a subprocess was found and signaled.
    """
    with _active_lock:
        p = _active.pop(session_id, None)
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
