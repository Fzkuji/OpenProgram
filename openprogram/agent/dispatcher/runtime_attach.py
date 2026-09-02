"""Runtime attach — wrap an @agentic_function block as a turn-visible
runtime block.

Extracted from dispatcher/__init__.py (dispatcher-split step 3, the
runtime_attach piece). ``_wrap_agentic_runtime_block`` takes an
@agentic_function AgentTool and returns a tool whose execute persists a
``display=runtime`` placeholder, streams the live Execution DAG, and
finalizes the row — so an LLM-issued call renders identically to a manual
``/run <fn>``. It depends only on the stdlib + ``types`` here; everything
heavy (SessionDB, SessionNodeWriter, build_exec_dag, the subprocess runner)
is pulled in via in-function local imports, so this stays a leaf.

The package ``__init__`` re-exports ``_wrap_agentic_runtime_block`` so
``from openprogram.agent.dispatcher import _wrap_agentic_runtime_block``
(process_runner.py) and the in-package callers resolve unchanged. The
phase-3 create_runtime + GraphStore wiring that currently lives inside
process_user_turn will join this module in a later step.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

import json
import logging
import os
import time

from openprogram.agent.dispatcher.types import (
    EventCallback,
    TurnRequest,
    _subprocess_terminal_status,
)

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
    """Wrap an @agentic_function AgentTool's execute so an LLM-issued
    call renders the same way as a manual ``/run <fn>`` invocation —
    a ``display=runtime`` row with the full Execution DAG, duration,
    parameters, and return preview.

    Before exec: set ``_call_id`` to the real caller so the
    @agentic_function decorator writes its top DAG node with
    ``predecessor`` pointing at that caller (build_exec_dag walks from
    that id to reconstruct the tree). No placeholder node is created.

    During exec: poll build_exec_dag and broadcast tree_update
    envelopes so live UIs fill the Execution DAG without a refresh.
    The code node written by @agentic_function is the canonical record.
    """
    from openprogram.agent.types import AgentTool as _AgentTool

    orig_execute = agent_tool.execute
    tool_name = agent_tool.name
    _is_agentic_tool = bool(getattr(agent_tool, "_is_agentic", False))
    _run_in_worker = bool(getattr(agent_tool, "_run_in_worker", False))

    async def _runtime_block_execute(call_id, args, cancel, on_update):
        from openprogram.agent.session_db import default_db
        from openprogram.agentic_programming.function import (
            _call_id as _call_id_var,
            _forced_predecessor as _forced_pred_var,
            _forced_node_id as _forced_node_var,
        )
        from openprogram.store import SessionNodeWriter
        from openprogram.webui._exec_dag import build_exec_dag

        db = default_db()
        # The code node written by @agentic_function is the canonical
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
        # publish it as ``_forced_node_id`` so the @agentic_function
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
        # Live Execution DAG streaming: poll build_exec_dag(...,
        # _real_caller) every ~1.2s while the tool runs and broadcast
        # tree_update envelopes (anchored on _real_caller) so the
        # RuntimeBlock's <ExecutionTree /> fills in live. Without this
        # the card sits empty until the result envelope lands.
        try:
            from openprogram.webui._exec_dag import (
                live_progress as _live_progress,
            )
            # In the @agentic_function subprocess the worker's
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
            if _is_agentic_tool and not _run_in_worker and not _in_subproc:
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
                from openprogram.agent.process_runner import (
                    agentic_subprocess_timeout_seconds,
                    run_agentic_in_subprocess,
                )
                import asyncio as _asyncio
                # Bridge events back. The child's wrapper will emit
                # its own placeholder + result envelopes; the ones
                # we already emitted above are anchored to the same
                # runtime_id so the second write is a no-op upsert.
                loop = _asyncio.get_event_loop()
                from openprogram.agent.authority import runtime_authority
                from openprogram.worktree.context import current_worktree_path
                subprocess_args = dict(args or {})

                def _run_subprocess():
                    surface_snapshot = req.surface_context
                    captured_surface = None
                    browser_surface = (
                        tool_name == "browser_agent"
                        or (
                            tool_name == "gui_agent"
                            and (
                                str(subprocess_args.get("surface") or "desktop")
                                .strip()
                                .lower()
                                == "browser"
                                or bool(subprocess_args.get("backend"))
                            )
                        )
                    )
                    if surface_snapshot is None and browser_surface:
                        from openprogram.agent import surface_context

                        try:
                            captured_surface = surface_context.capture_pages()
                        except RuntimeError:
                            captured_surface = surface_context.window_context()
                        surface_snapshot = captured_surface
                    timeout_seconds = agentic_subprocess_timeout_seconds(
                        tool_name, subprocess_args,
                    )
                    try:
                        return run_agentic_in_subprocess(
                            tool_name=tool_name,
                            kwargs=subprocess_args,
                            session_id=req.session_id,
                            anchor_msg_id=assistant_msg_id,
                            work_dir=current_worktree_path(),
                            on_event=on_event,
                            # LLM-driven: pass the LLM's tool_call_id so the
                            # subprocess writes its placeholder under the
                            # SAME runtime_id the parent persisted, instead
                            # of inventing a ``forced_<random>`` and leaving
                            # us with two orphan placeholders for one call.
                            parent_call_id=call_id,
                            authority=runtime_authority(
                                req, f"agentic/{tool_name}"
                            ),
                            permission_rules_snapshot=(
                                _permission_rules_snapshot(req.permission_rules)
                            ),
                            surface_context_snapshot=surface_snapshot,
                            render_range=req.render_range,
                            timeout_seconds=timeout_seconds,
                        )
                    finally:
                        if captured_surface is not None:
                            from openprogram.agent.surface_context import (
                                release_bindings,
                            )

                            release_bindings(captured_surface)

                subprocess_started_at = time.time()
                out = await loop.run_in_executor(
                    None,
                    _run_subprocess,
                )
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
                            from openprogram.agent.run_control import (
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
                    from openprogram.agent.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.providers.types import (
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
                    from openprogram.agent.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.providers.types import (
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
                    from openprogram.agent.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.providers.types import (
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
                    from openprogram.agent.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.providers.types import (
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

        # Finalize the placeholder.
        try:
            text_out = "".join(
                c.text for c in (result.content or [])
                if hasattr(c, "text") and isinstance(c.text, str)
            )
        except Exception:
            text_out = ""
        # The @agentic_function ran in a spawn()'d subprocess (see
        # process_runner.py). That child wrote every nested code /
        # tool / LLM Call directly to the session's git history via
        # its OWN SessionStore. The parent worker's cached
        # SessionMemoryIndex never observed those writes, so any
        # subsequent build_branches_payload / get_messages would
        # return the pre-subprocess snapshot — missing the gui_agent
        # square and all of its sub-call children, leaving the
        # mini-DAG showing only the conv chain (user / llm reply /
        # runtime placeholder). Drop the cache so build_exec_dag
        # below + the broadcast list_branches both see the on-disk
        # truth.
        try:
            db.invalidate_cache(req.session_id)
        except Exception:
            _log.debug("session cache invalidation failed for %s",
                       req.session_id, exc_info=True)
        # DEBUG: inspect what build_exec_dag sees after invalidate_cache.
        # Gated behind ``OPENPROGRAM_DEBUG_DISPATCHER`` because the
        # ``[dispatcher.debug]`` line was landing in the user-facing chat
        # transcript on every tool call. Useful for debugging the
        # exec-DAG / runtime-finalize wiring; off by default.
        import os as _os
        if _os.environ.get("OPENPROGRAM_DEBUG_DISPATCHER", "").strip() in ("1", "true", "yes"):
            import sys as _sys
            try:
                _dbg_nodes = db.get_nodes(req.session_id)
                _dbg_kids = [n for n in _dbg_nodes
                             if n.is_code() and n.name == tool_name
                             and n.caller == _real_caller]
                _dbg_total = len(_dbg_nodes)
                _dbg_top_id = _dbg_kids[-1].id if _dbg_kids else None
                _dbg_grand = sum(1 for n in _dbg_nodes
                                 if n.caller == _dbg_top_id) if _dbg_top_id else 0
                print(f"[dispatcher.debug] finalize tool={tool_name} "
                      f"caller={_real_caller} total_nodes={_dbg_total} "
                      f"top_match={bool(_dbg_top_id)} grand_children={_dbg_grand}",
                      file=_sys.stderr, flush=True)
            except Exception as _e:
                print(f"[dispatcher.debug] inspect failed: {_e}",
                      file=_sys.stderr, flush=True)
        tree_dict = build_exec_dag(req.session_id, tool_name, _real_caller) or {
            "path": tool_name,
            "name": tool_name,
            "params": {k: v for k, v in (args or {}).items() if k != "runtime"},
            "output": text_out,
            "status": "completed",
        }
        done_at = time.time()
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
    for _attr in ("_is_agentic", "_defer", "_run_in_worker"):
        # A frozen/slotted tool object rejects the copy; the wrapper just
        # loses an optional marker attribute.
        try:
            setattr(wrapped, _attr, getattr(agent_tool, _attr, None))
        except AttributeError:
            _log.debug("could not copy %s onto tool wrapper", _attr)
    return wrapped
