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
import uuid
from typing import Any, Callable, Optional


# execution_id → live Process. Session is a secondary index so a
# compatibility session-level lookup still finds the current owner.
_active: dict[str, mp.Process] = {}
_active_stop_q: dict[str, "mp.Queue"] = {}
_session_execution: dict[str, str] = {}
_active_lock = threading.Lock()


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


def _bridge_webtab_to_parent(data: dict, answer_queue) -> None:
    command = data.get("command") if isinstance(data, dict) else None
    bound_activate = (
        isinstance(command, dict)
        and command.get("op") == "activate"
        and isinstance(command.get("binding_id"), str)
    )
    if not isinstance(command, dict) or (
        command.get("op") not in {"open", "active"} and not bound_activate
    ):
        result = {"ok": False, "error": "unsupported webtab bridge operation"}
    else:
        try:
            timeout = max(0.1, min(float(data.get("timeout", 15)), 15.0))
            from openprogram.webui.ws_actions import webtab

            binding_id = command.get("binding_id")
            if bound_activate:
                result = webtab.request_bound_tab(
                    binding_id,
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
            else:
                result = webtab._request(command, timeout)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    answer_queue.put({
        "__op_webtab_result__": True,
        "req_id": data.get("req_id") if isinstance(data, dict) else None,
        "result": result,
    })


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

    # --- user-input subprocess bridge: answer side (user-input-requests.md Phase 2) ---
    # The child blocks in runtime.ask on its LOCAL QuestionRegistry. The parent
    # routes the user's reply back through ``answer_queue``; this pump resolves
    # the local registry so the blocked ask returns. (The ask SIDE — sending the
    # question UP — is wired below as a QueueTransport on the child's runtime,
    # once that runtime exists.)
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
        questions=list(data.get("questions") or []),  # kind="ask_many": carry too
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
    authority: Optional[dict] = None,
    permission_rules_snapshot: Optional[dict] = None,
    surface_context_snapshot: Optional[dict] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    response_format=None,
    render_range: Optional[dict[str, int]] = None,
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

    p = ctx.Process(
        target=_child_entry,
        args=(tool_name, dict(kwargs or {}), session_id, anchor_msg_id,
              work_dir, result_path, event_queue, parent_call_id,
              answer_queue, stop_queue, response_format_snapshot,
              render_range, usage_ctx_snapshot, sandbox_policy_snapshot,
              authority, permission_rules_snapshot, surface_context_snapshot,
              provider, model),
        daemon=False,
    )
    p.start()

    eid = execution_id or parent_call_id or session_id
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
    stop_flag = threading.Event()
    # qids this subprocess has asked about, so kill/cleanup can decline
    # them (and their parent-side waiter threads exit).
    pending_qids: set[str] = set()
    pending_qids_lock = threading.Lock()

    def _handle(env) -> None:
        if isinstance(env, dict) and env.get("__op_webtab__"):
            _bridge_webtab_to_parent(env.get("data") or {}, answer_queue)
            return
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
