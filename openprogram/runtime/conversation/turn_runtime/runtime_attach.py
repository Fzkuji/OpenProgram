"""Runtime attach — wrap an Agent method block as a turn-visible
runtime block.

Extracted from dispatcher/__init__.py (dispatcher-split step 3, the
runtime_attach piece). ``_wrap_agentic_runtime_block`` takes an
Agent method AgentTool and returns a tool whose execute persists a
``display=runtime`` placeholder, streams the live Execution DAG, and
finalizes the row — so an LLM-issued call renders identically to a manual
``/run <fn>``. It depends only on the stdlib + ``types`` here; everything
heavy (SessionDB, SessionNodeWriter, build_exec_dag, the subprocess runner)
is pulled in via in-function local imports, so this stays a leaf.

The package ``__init__`` re-exports ``_wrap_agentic_runtime_block`` so
``from openprogram.runtime.conversation.turn_runtime import _wrap_agentic_runtime_block``
(process_runner.py) and the in-package callers resolve unchanged. The
phase-3 create_runtime + GraphStore wiring that currently lives inside
process_user_turn will join this module in a later step.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time

from openprogram.runtime.conversation.turn_runtime.types import (
    EventCallback,
    _subprocess_terminal_status,
)
from openprogram.runtime.agent.turn_request import TurnRequest

_log = logging.getLogger(__name__)


def _permission_rules_snapshot(rules) -> dict | None:
    if rules is None:
        return None
    def values(name: str) -> list:
        value = rules.get(name) if isinstance(rules, dict) else getattr(rules, name, None)
        return list(value or [])
    return {name: values(name) for name in ("allow", "deny", "ask")}


def _wrap_agentic_runtime_block(
    agent_tool,
    req: "TurnRequest",
    on_event: EventCallback,
    assistant_msg_id: str,
):
    """Wrap an Agent method AgentTool's execute so an LLM-issued
    call renders the same way as a manual ``/run <fn>`` invocation —
    a ``display=runtime`` row with the full Execution DAG, duration,
    parameters, and return preview.

    Before exec: set ``_call_id`` to the real caller so the
    Agent method decorator writes its top DAG node with
    ``predecessor`` pointing at that caller (build_exec_dag walks from
    that id to reconstruct the tree). No placeholder node is created.

    During exec: poll build_exec_dag and broadcast tree_update
    envelopes so live UIs fill the Execution DAG without a refresh.
    The code node written by Agent method is the canonical record.
    """
    from openprogram.models.providers.types import AgentTool as _AgentTool

    orig_execute = agent_tool.execute
    tool_name = agent_tool.name
    _is_agent_method_tool = bool(getattr(agent_tool, "_is_agent_method", False))
    _run_in_worker = bool(getattr(agent_tool, "_run_in_worker", False))

    async def _runtime_block_execute(call_id, args, cancel, on_update):
        from openprogram.runtime.conversation.turn_request_context import capture_original_owner_input
        try:
            original_owner_input = capture_original_owner_input(req)
        except Exception:
            # Unavailable source data cannot prevent an unrelated program
            # from executing, and cannot become report evidence.
            original_owner_input = None
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.context.scope import (
            _call_id as _call_id_var,
            _forced_predecessor as _forced_pred_var,
        )
        from openprogram.runtime.agent.call_state import (
            _forced_node_id as _forced_node_var,
        )
        from openprogram.state.store import SessionNodeWriter

        db = default_db()
        # The code node written by Agent method is the canonical
        # record — no placeholder is persisted or broadcast. Live
        # progress comes via tree_update events from the live_progress
        # poller below.

        # ``assistant_msg_id`` is the caller for an LLM-issued call (the
        # LLM reply id) or a fn-form's caller. A RETRY encodes its fork
        # point as ``pred:<id>``: the run must land as a SIBLING of the
        # original (the predecessor field = that id, empty caller), NOT as
        # a sub-call of it — so decode it into ``_forced_predecessor`` and
        # leave the caller empty. Every top-level run then uses the same
        # predecessor edge; only internal sub-calls carry a code caller.
        # A top-level card the PARENT pre-created before spawning encodes
        # its node id as a ``|node:<id>`` suffix on the anchor. Strip it
        # first (it rides on both the ``pred:`` and raw-caller forms) and
        # publish it as ``_forced_node_id`` so the Agent method
        # wrapper REUSES that id instead of minting + appending a second
        # top-level node.
        _anchor = assistant_msg_id
        _node_token = None
        if isinstance(_anchor, str) and "|node:" in _anchor:
            _anchor, _forced_nid = _anchor.split("|node:", 1)
            if _forced_nid:
                _node_token = _forced_node_var.set(_forced_nid)
        _pred_token = None
        if isinstance(_anchor, str) and _anchor.startswith("pred:"):
            _pred_token = _forced_pred_var.set(_anchor[len("pred:"):])
            _real_caller = ""
        else:
            _real_caller = _anchor
        _call_token = _call_id_var.set(_real_caller)
        # execution_stream.v1 transport: nested llm() emits through on_event
        # (in-process broadcast or subprocess queue). display_msg_id is the
        # tree/card anchor — not the LLM node id.
        _stream_tok = None
        _display_tok = None
        try:
            from openprogram.runtime.execution.run_control import (
                get_current_execution_id,
            )
            from openprogram.runtime.agent.runtime.execution_stream import (
                bind_transport_for_turn,
            )
            from openprogram.runtime.execution import default_store

            _eid = get_current_execution_id() or _real_caller or req.session_id
            _gen = 1
            try:
                _owner = default_store().get_execution(_eid) if _eid else None
                if _owner is not None:
                    _gen = int((_owner.owner_lease or {}).get("generation") or 1)
            except Exception:
                _gen = 1
            _stream_tok, _display_tok = bind_transport_for_turn(
                session_id=req.session_id,
                execution_id=str(_eid),
                generation=_gen,
                display_msg_id=_real_caller or assistant_msg_id or "",
                on_event=on_event,
            )
        except Exception:
            _log.debug("execution_stream transport bind failed", exc_info=True)
        # Live Execution DAG streaming: poll build_exec_dag(...,
        # _real_caller) every ~1.2s while the tool runs and broadcast
        # tree_update envelopes (anchored on _real_caller) so the
        # RuntimeBlock's <ExecutionTree /> fills in live. Without this
        # the card sits empty until the result envelope lands.
        try:
            from openprogram.runtime.execution.dag import (
                live_progress as _live_progress,
            )
            # In the Agent method subprocess the worker's
            # ``_broadcast_*`` globals point at an empty ws-clients set
            # — there's no parent process here. Route progress
            # envelopes through ``on_event`` instead so the subprocess
            # writes onto its mp.Queue and the parent's drain thread
            # does the actual fanout. In-process runs leave on_event
            # at the dispatcher's default (worker broadcast wrapper)
            # and the same code path keeps working unchanged.
            _live_ctx = _live_progress(
                req.session_id, _real_caller, tool_name, on_event=on_event,
            )
        except Exception:
            _live_ctx = None
        if _live_ctx is not None:
            _live_ctx.__enter__()
        try:
            _in_subproc = os.environ.get(
                "OPENPROGRAM_IN_AGENTIC_SUBPROCESS"
            ) == "1"
            if _is_agent_method_tool and not _run_in_worker and not _in_subproc:
                # Route through a fork()'d subprocess so canonical
                # execution cancellation's
                # SIGKILL kills the tool in milliseconds. The child
                # re-installs the wrapper itself and bridges events
                # back, but to keep the runtime-block we already
                # persisted above as the single source of truth, we
                # call orig_execute directly inside the child (no
                # nested wrap). NOTE: we cannot re-use this wrapper
                # in the child because it would re-persist the
                # placeholder. So we go via a dedicated child entry
                # that targets the tool's raw execute via the
                # subprocess runner, which itself re-applies the
                # wrapper inside the child. The duplicate placeholder
                # write is idempotent (db.append_message on same id
                # upserts) — acceptable.
                from openprogram.runtime.conversation.process_runner import (
                    agentic_subprocess_limits,
                    run_agent_method_in_subprocess,
                )
                import asyncio as _asyncio
                # Bridge events back. The child's wrapper will emit
                # its own placeholder + result envelopes; the ones
                # we already emitted above are anchored to the same
                # runtime_id so the second write is a no-op upsert.
                loop = _asyncio.get_event_loop()
                from openprogram.runtime.permissions.authority import runtime_authority
                from openprogram.state.worktree.context import current_worktree_path
                subprocess_args = dict(args or {})

                from openprogram.runtime.execution.run_control import (
                    get_current_execution_id,
                )
                from openprogram.runtime.execution import default_store
                execution_id = get_current_execution_id()
                owner = default_store().get_execution(execution_id) if execution_id else None
                owner_attempt_id = owner.current_attempt_id if owner else None
                owner_generation = owner.owner_lease.get("generation") if owner else None

                from openprogram.runtime.conversation import turn_runtime as dispatcher
                profile = (req.profile_snapshot if req.profile_snapshot is not None
                           else dispatcher._load_agent_profile(req.agent_id))
                from openprogram.models.providers.utils.errors import LLMError
                selected_model = None
                model_setup_error = None
                try:
                    selected_model = dispatcher._resolve_model(profile, req.model_override)
                except LLMError as error:
                    # Model-free Programs still execute; the child retains
                    # this terminal selection failure for actual model calls.
                    model_setup_error = {
                        "message": error.message, "reason": error.reason.value,
                        "retryable": error.retryable,
                    }
                work_dir = current_worktree_path()

                def _run_subprocess():
                    from openprogram.runtime.permissions.lifecycle import current_permission_request

                    from openprogram.runtime.permissions.identity import detached_from_window

                    effective_req = current_permission_request(req)
                    detached = detached_from_window(req)
                    surface_snapshot = req.surface_context
                    captured_surface = None
                    if surface_snapshot is None:
                        from openprogram.runtime.conversation import surface_context
                        from openprogram.runtime.permissions.policy import permission_decision, surface_grant_applies
                        from openprogram.programs import get_agent_tool

                        screen_tool = get_agent_tool("computer_use_agent")
                        list_tabs = {"target": "browser", "action": "tabs"}
                        decision = permission_decision(
                            screen_tool, effective_req, list_tabs,
                        ) if screen_tool is not None else None
                        if (decision is not None and decision.outcome == "allow"
                                and surface_grant_applies("computer_use_agent", effective_req, list_tabs)):
                            try:
                                captured_surface = surface_context.capture_pages()
                            except RuntimeError:
                                # An unavailable inventory cannot create a Page grant.
                                pass
                            surface_snapshot = (
                                surface_context.without_primary_page(captured_surface)
                                if detached and captured_surface else captured_surface
                            )
                    # The agent gets its budget as max_seconds so it stops
                    # itself and returns its history; the process is killed
                    # only if it overruns the budget plus a grace period.
                    limits = agentic_subprocess_limits(
                        tool_name, subprocess_args,
                    )
                    try:
                        return run_agent_method_in_subprocess(
                            tool_name=tool_name,
                            kwargs=limits.kwargs,
                            session_id=req.session_id,
                            anchor_msg_id=assistant_msg_id,
                            work_dir=work_dir,
                            provider=selected_model.provider if selected_model else None,
                            model=selected_model.id if selected_model else None,
                            model_setup_error=model_setup_error,
                            on_event=on_event,
                            # LLM-driven: pass the LLM's tool_call_id so the
                            # subprocess writes its placeholder under the
                            # SAME runtime_id the parent persisted, instead
                            # of inventing a ``forced_<random>`` and leaving
                            # us with two orphan placeholders for one call.
                            parent_call_id=call_id,
                            execution_id=execution_id,
                            attempt_id=owner_attempt_id,
                            generation=owner_generation,
                            authority=runtime_authority(
                                req, f"agentic/{tool_name}"
                            ),
                            original_owner_input=original_owner_input,
                            permission_rules_snapshot=(
                                _permission_rules_snapshot(effective_req.permission_rules)
                            ),
                            surface_context_snapshot=surface_snapshot,
                            detached=detached,
                            permission_mode_snapshot=effective_req.permission_mode,
                            render_range=req.render_range,
                            timeout_seconds=limits.timeout_seconds,
                            budget_seconds=limits.budget_seconds,
                        )
                    finally:
                        if captured_surface is not None:
                            from openprogram.runtime.conversation.surface_context import (
                                release_bindings,
                            )

                            release_bindings(captured_surface)

                subprocess_started_at = time.time()
                out = await loop.run_in_executor(
                    None,
                    contextvars.copy_context().run, _run_subprocess,
                )
                if out.get("function_suspended"):
                    from openprogram.runtime.agent.durable_steps import FunctionSuspended
                    raise FunctionSuspended()
                cleanup_result = out.get("page_cleanup_result")
                if out.get("page_cleanup_failed") and isinstance(
                    cleanup_result, dict,
                ):
                    try:
                        db.invalidate_cache(req.session_id)
                        cleanup_node_id = out.get("runtime_msg_id")
                        if not cleanup_node_id:
                            candidates = [
                                node for node in db.get_nodes(req.session_id)
                                if node.is_code()
                                and node.name == tool_name
                                and node.caller == _real_caller
                                and node.created_at >= subprocess_started_at
                            ]
                            running = [
                                node for node in candidates
                                if (node.metadata or {}).get("status") == "running"
                            ]
                            matches = running or candidates
                            if len(matches) == 1:
                                cleanup_node_id = matches[0].id
                        if cleanup_node_id:
                            from openprogram.runtime.execution.run_control import (
                                mark_execution_terminal,
                                resume_cancel,
                            )

                            cleanup_node = next(
                                (
                                    node for node in db.get_nodes(req.session_id)
                                    if node.id == cleanup_node_id
                                ),
                                None,
                            )
                            cleanup_metadata = (
                                (cleanup_node.metadata or {})
                                if cleanup_node is not None else {}
                            )
                            if cleanup_metadata.get("status") == "cancelling":
                                resume_cancel(cleanup_node_id)
                            else:
                                mark_execution_terminal(
                                    cleanup_node_id,
                                    _subprocess_terminal_status(
                                        out,
                                        cleanup_metadata,
                                    ),
                                    store=db,
                                )
                            SessionNodeWriter(db, req.session_id).update(
                                cleanup_node_id,
                                output=cleanup_result,
                                metadata={"last_update_at": time.time()},
                            )
                    except Exception:
                        _log.warning(
                            "failed to persist Page cleanup handoff for %s",
                            out.get("runtime_msg_id") or call_id,
                            exc_info=True,
                        )
                    from openprogram.models.providers.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.models.providers.types import (
                        TextContent as _CB,
                    )
                    result = _TR(
                        content=[_CB(text=json.dumps(
                            cleanup_result,
                            ensure_ascii=False,
                        ))],
                        details=cleanup_result,
                        is_error=False,
                    )
                elif out.get("error"):
                    from openprogram.models.providers.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.models.providers.types import (
                        TextContent as _CB,
                    )
                    details = {
                        "reason_code": (
                            "agentic_subprocess_timeout"
                            if out.get("timed_out")
                            else "agentic_subprocess_error"
                        )
                    }
                    if out.get("timed_out"):
                        details["timed_out"] = True
                        if out.get("progress") is not None:
                            details["progress"] = out["progress"]
                    if out.get("killed"):
                        details["killed"] = True
                    if out.get("signal") is not None:
                        details["signal"] = out["signal"]
                    result = _TR(
                        content=[_CB(text=str(out["error"]))],
                        details=details,
                        is_error=True,
                    )
                elif out.get("killed"):
                    from openprogram.models.providers.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.models.providers.types import (
                        TextContent as _CB,
                    )
                    details = {
                        "reason_code": "agentic_subprocess_cancelled",
                        "cancelled": True,
                        "killed": True,
                    }
                    if out.get("signal") is not None:
                        details["signal"] = out["signal"]
                    result = _TR(
                        content=[_CB(text="[cancelled by user]")],
                        details=details,
                        is_error=True,
                    )
                else:
                    from openprogram.models.providers.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.models.providers.types import (
                        TextContent as _CB,
                    )
                    result = _TR(content=[_CB(text=out.get("text") or "")])
            else:
                result = await orig_execute(call_id, args, cancel, on_update)
        finally:
            # ContextVar.reset has exactly two tolerable failures, and
            # this teardown must survive both: ValueError for a token
            # minted in another context, RuntimeError for one already
            # spent. A cancelled turn reaches here with a spent token,
            # and letting that escape replaces the CancelledError — the
            # turn then never reports as cancelled and its children keep
            # running. Both leave the var where the other context put it.
            try:
                _call_id_var.reset(_call_token)
            except (ValueError, RuntimeError):
                _log.debug("call-id token already spent or foreign",
                           exc_info=True)
            if _pred_token is not None:
                try:
                    _forced_pred_var.reset(_pred_token)
                except (ValueError, RuntimeError):
                    _log.debug("predecessor token already spent or foreign",
                               exc_info=True)
            if _node_token is not None:
                try:
                    _forced_node_var.reset(_node_token)
                except (ValueError, RuntimeError):
                    _log.debug("node token already spent or foreign",
                               exc_info=True)
            if _live_ctx is not None:
                try:
                    _live_ctx.__exit__(None, None, None)
                except Exception:
                    _log.debug("live-context teardown failed", exc_info=True)
            try:
                from openprogram.runtime.agent.runtime.execution_stream import (
                    reset_display_msg_id,
                    reset_stream_transport,
                )
                if _display_tok is not None:
                    reset_display_msg_id(_display_tok)
                if _stream_tok is not None:
                    reset_stream_transport(_stream_tok)
            except Exception:
                _log.debug("execution_stream transport reset failed", exc_info=True)

        # The Agent method ran in a spawn()'d subprocess (see
        # process_runner.py). That child wrote every nested code /
        # tool / LLM Call directly to the session's log history via
        # its OWN SessionStore. The parent worker's cached
        # SessionMemoryIndex never observed those writes, so any
        # subsequent build_branches_payload / get_messages would
        # return the pre-subprocess snapshot — missing the Agent method's
        # square and all of its sub-call children, leaving the
        # mini-DAG showing only the conv chain (user / llm reply /
        # runtime placeholder). Drop the cache so later readers (the
        # broadcast list_branches, a refresh) see the on-disk truth.
        try:
            db.invalidate_cache(req.session_id)
        except Exception:
            _log.debug("session cache invalidation failed for %s",
                       req.session_id, exc_info=True)
        # DEBUG: inspect the session's nodes after invalidate_cache.
        # Gated behind ``OPENPROGRAM_DEBUG_DISPATCHER`` because the
        # ``[dispatcher.debug]`` line was landing in the user-facing chat
        # transcript on every tool call. Useful for debugging the
        # exec-DAG / runtime-finalize wiring; off by default.
        import os as _os
        if _os.environ.get("OPENPROGRAM_DEBUG_DISPATCHER", "").strip() in ("1", "true", "yes"):
            try:
                _dbg_nodes = db.get_nodes(req.session_id)
                _dbg_kids = [n for n in _dbg_nodes
                             if n.is_code() and n.name == tool_name
                             and n.caller == _real_caller]
                _dbg_total = len(_dbg_nodes)
                _dbg_top_id = _dbg_kids[-1].id if _dbg_kids else None
                _dbg_grand = sum(1 for n in _dbg_nodes
                                 if n.caller == _dbg_top_id) if _dbg_top_id else 0
                _log.info("dispatcher debug: finalize tool=%s caller=%s total_nodes=%s "
                          "top_match=%s grand_children=%s", tool_name, _real_caller,
                          _dbg_total, bool(_dbg_top_id), _dbg_grand)
            except Exception as _e:
                _log.info("dispatcher debug: inspect failed: %s", _e)
        # No result broadcast — the code node in SessionStore is the
        # canonical record. The chat UI already has the live progress
        # tree from tree_update events. Refreshing loads from SessionStore.
        return result

    wrapped = _AgentTool(
        name=agent_tool.name,
        description=agent_tool.description,
        parameters=agent_tool.parameters,
        label=getattr(agent_tool, "label", agent_tool.name) or agent_tool.name,
        execute=_runtime_block_execute,
    )
    for _attr in (
        "_is_agent_method", "_python_callable", "_method_options", "_dag_expose", "_defer", "_tier", "_program_owner", "_workflow_owner", "_run_in_worker", "_mcp_server",
        "_runtime_implementation", "_requires_approval", "_accept_edits_safe",
        "_permission_preflight", "_permission_managed", "_permission_visible",
        "_permission_epoch_visible",
    ):
        # A frozen/slotted tool object rejects the copy; the wrapper just
        # loses an optional marker attribute.
        try:
            setattr(wrapped, _attr, getattr(agent_tool, _attr, None))
        except AttributeError:
            _log.debug("could not copy %s onto tool wrapper", _attr)
    manifest = getattr(agent_tool, "_interaction_manifest", None)
    if callable(manifest):
        try:
            setattr(wrapped, "_interaction_manifest", manifest)
        except AttributeError:
            _log.debug("could not copy _interaction_manifest onto tool wrapper")
    return wrapped
