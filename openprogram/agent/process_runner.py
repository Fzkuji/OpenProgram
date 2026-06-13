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
    answer_queue: "Optional[mp.Queue]" = None,
) -> None:
    # Detach into our own process group so ``killpg`` from the parent
    # takes down every grandchild (browser, subprocess providers, ...).
    try:
        os.setpgrp()
    except Exception:
        pass

    # --- user-input subprocess bridge: answer side (user-input-requests.md Phase 2) ---
    # The child blocks in runtime.ask on its LOCAL QuestionRegistry. The parent
    # routes the user's reply back through ``answer_queue``; this pump resolves
    # the local registry so the blocked ask returns. (The ask SIDE — sending the
    # question UP — is wired below as a QueueTransport on the child's runtime,
    # once that runtime exists.)
    if answer_queue is not None:
        try:
            from openprogram.agent.questions import get_question_registry

            def _answer_pump() -> None:
                reg = get_question_registry()
                while True:
                    try:
                        msg = answer_queue.get()
                    except Exception:
                        return
                    if msg is None:  # shutdown sentinel
                        return
                    try:
                        qid = msg.get("id")
                        outcome = msg.get("outcome") or "declined"
                        value = msg.get("value")
                        if qid:
                            reg.resolve(qid, outcome, value)
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
# user-input subprocess bridge (parent side) — Phase 2
# ---------------------------------------------------------------------------
#
# The child raised a runtime.ask question and pushed its envelope up through
# the event queue. On the parent side we:
#   1. register it on the PARENT QuestionRegistry (reusing the child's qid so
#      the WS reply handler routes the answer to the same id),
#   2. emit it onto the event layer so the frontend draws the question card
#      (the same default exit runtime.ask uses in the worker process),
#   3. wait for the parent registry to be resolved (by the WS handler or by a
#      stop/cancel), then push the answer back to the child via answer_queue.
#
# The parent registry's Event is what the WS handler sets via resolve(); a
# small waiter thread bridges that to the answer_queue the child blocks on.

def _bridge_question_to_parent(data, answer_queue, pending_qids, lock) -> None:
    try:
        from openprogram.agent.questions import (
            PendingQuestion, get_question_registry, emit_question_asked,
        )
    except Exception:
        return

    qid = data.get("id")
    if not qid:
        return

    reg = get_question_registry()
    q = PendingQuestion(
        id=qid,
        session_id=data.get("session_id") or "",
        kind=data.get("kind") or "ask",
        prompt=data.get("prompt") or "",
        options=list(data.get("options") or []),
        multi=bool(data.get("multi")),
        allow_custom=bool(data.get("allow_custom", True)),
        detail=data.get("detail") or "",
        schema=dict(data.get("schema") or {}),  # kind="form": carry fields over
        created_at=data.get("created_at") or 0.0,
        expires_at=data.get("expires_at") or 0.0,
    )
    ev = reg.register(q)
    with lock:
        pending_qids.add(qid)

    # Draw the frontend card (and put it on the event stream) exactly as an
    # in-worker runtime.ask would — no transport passed, so this goes through
    # the default EventLayerTransport.
    emit_question_asked(data)

    def _wait_and_forward() -> None:
        try:
            ev.wait()  # set by registry.resolve() (WS reply / stop)
            res = reg.consume(qid)
        except Exception:
            res = None
        with lock:
            pending_qids.discard(qid)
        outcome, value = res if res is not None else ("declined", None)
        try:
            answer_queue.put({"id": qid, "outcome": outcome, "value": value},
                             block=False)
        except Exception:
            pass

    threading.Thread(target=_wait_and_forward, daemon=True).start()


def _decline_bridged_question(qid: str) -> None:
    """Child gone (exited / killed) with a question still open — decline it.
    resolve() wakes the waiter thread (which then no-ops pushing to a dead
    child) and the WS broadcast retracts the frontend card."""
    try:
        from openprogram.webui.ws_actions.session import _resolve_question
        _resolve_question(qid, "declined", None)
    except Exception:
        try:
            from openprogram.agent.questions import get_question_registry
            get_question_registry().resolve(qid, "declined", None)
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
    # parent→child answer channel (user-input-requests.md Phase 2): the
    # child blocks in runtime.ask; the parent routes the user's reply back
    # through this queue so the child's local registry can wake the call.
    answer_queue: mp.Queue = ctx.Queue()
    p = ctx.Process(
        target=_child_entry,
        args=(tool_name, dict(kwargs or {}), session_id, anchor_msg_id,
              work_dir, result_path, event_queue, parent_call_id,
              answer_queue),
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
    # qids this subprocess has asked about, so kill/cleanup can decline
    # them (and their parent-side waiter threads exit).
    pending_qids: set[str] = set()
    pending_qids_lock = threading.Lock()

    def _handle(env) -> None:
        # Intercept the user-input bridge envelope: a question the child
        # raised via runtime.ask. Register it on the PARENT registry +
        # broadcast to the frontend, and arrange to route the answer back
        # through ``answer_queue``.
        if isinstance(env, dict) and env.get("__op_question__"):
            _bridge_question_to_parent(
                env.get("data") or {}, answer_queue,
                pending_qids, pending_qids_lock,
            )
            return
        try:
            if on_event:
                on_event(env)
        except Exception:
            pass

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
                        _handle(env2)
                continue
            _handle(env)

    drain_thread = threading.Thread(target=_drain, daemon=True)
    drain_thread.start()

    try:
        p.join()
    finally:
        stop_flag.set()
        # Child is gone — decline any still-pending questions so their
        # parent-side waiter threads exit and any open frontend cards get
        # retracted (question.rejected). Nothing left to answer.
        with pending_qids_lock:
            leftover = list(pending_qids)
        for _qid in leftover:
            _decline_bridged_question(_qid)
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
