"""Runtime attach — wrap an @agentic_function block as a turn-visible
runtime block.

Extracted from dispatcher/__init__.py (dispatcher-split step 3, the
runtime_attach piece). ``_wrap_agentic_runtime_block`` takes an
@agentic_function AgentTool and returns a tool whose execute persists a
``display=runtime`` placeholder, streams the live Execution DAG, and
finalizes the row — so an LLM-issued call renders identically to a manual
``/run <fn>``. It depends only on the stdlib + ``types`` here; everything
heavy (SessionDB, GraphStoreShim, build_exec_dag, the subprocess runner)
is pulled in via in-function local imports, so this stays a leaf.

The package ``__init__`` re-exports ``_wrap_agentic_runtime_block`` so
``from openprogram.agent.dispatcher import _wrap_agentic_runtime_block``
(process_runner.py) and the in-package callers resolve unchanged. The
phase-3 create_runtime + GraphStore wiring that currently lives inside
process_user_turn will join this module in a later step.

See docs/design/runtime/dispatcher-split.md.
"""
from __future__ import annotations

import os
import time

from openprogram.agent.dispatcher.types import EventCallback, TurnRequest


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

    Before exec: persist a ``role=assistant, type=status,
    display=runtime, status=running`` placeholder and broadcast it.
    Set ``_call_id`` so the @agentic_function decorator anchors its
    top DAG node under our placeholder id (build_exec_dag walks from
    that id to reconstruct the tree).

    After exec: rebuild the exec DAG, update the placeholder in place
    with ``status=done`` + ``context_tree`` + final output, broadcast
    a runtime-block result envelope so live UIs flip without a
    refresh.
    """
    from openprogram.agent.types import AgentTool as _AgentTool

    orig_execute = agent_tool.execute
    tool_name = agent_tool.name
    _is_agentic_tool = bool(getattr(agent_tool, "_is_agentic", False))

    async def _runtime_block_execute(call_id, args, cancel, on_update):
        from openprogram.agent.session_db import default_db
        from openprogram.agentic_programming.function import (
            _call_id as _call_id_var,
        )
        from openprogram.store import GraphStoreShim
        from openprogram.webui._exec_dag import build_exec_dag

        # ``call_id`` here is the LLM's tool_call_id — unique per call,
        # so multiple invocations of the same agentic tool in one turn
        # each get their own runtime-block row.
        runtime_id = f"{assistant_msg_id}_rt_{call_id}"
        now = time.time()
        # Placeholder is structurally a tool call. Set BOTH parent_id
        # (so the frontend's ``_collapseRuntimePlaceholders`` anchor-
        # fold still sees the fn-form user-runtime anchor as the
        # placeholder's parent and can fold it) AND caller (so
        # ``conv_parent_of`` returns null because caller wins — the
        # placeholder doesn't count as a conv-child and lane.py /
        # branches_list don't treat it as a fork). Net effect:
        #   * LLM-called: parent=reply (assistant), caller=reply.
        #     ``_collapseRuntimePlaceholders`` matches LLM-called rule
        #     (reply.role==assistant) and folds the pair.
        #   * fn-form:    parent=anchor (user-runtime), caller=anchor.
        #     The pass's anchor-fold (anchor.role==user,
        #     anchor.display==runtime) removes the user circle so the
        #     mini-DAG shows ONE square on main trunk.
        placeholder = {
            "id": runtime_id,
            "role": "assistant",
            "type": "status",
            "content": "",
            "function": tool_name,
            "display": "runtime",
            "status": "running",
            "started_at": now,
            "last_update_at": now,
            "timestamp": now,
            "parent_id": assistant_msg_id,
            "caller": assistant_msg_id,
            "called_by": assistant_msg_id,
            "source": req.source,
            "agent_id": req.agent_id,
        }
        db = default_db()
        # No longer write placeholder to SessionStore — it's UI
        # scaffolding for the chat area, not a DAG node. The real
        # function call is the code node written by @agentic_function.
        on_event({
            "type": "chat_response",
            "data": {
                "type": "status",
                "session_id": req.session_id,
                "msg_id": runtime_id,
                "content": "",
                "function": tool_name,
                "display": "runtime",
                "status": "running",
                "parent_id": assistant_msg_id,
                "timestamp": now,
            },
        })

        # Set _call_id to the real caller so the @agentic_function
        # decorator's code node has called_by = the actual invoker:
        #   fn-form (user manual call): called_by = ROOT
        #   LLM call: called_by = assistant_msg_id (the LLM reply)
        _real_caller = assistant_msg_id
        if req.source == "fn-form" or placeholder.get("display") == "runtime":
            # For fn-form, assistant_msg_id is the anchor's reply id
            # which is a fake node. Walk up to ROOT.
            try:
                _parent_node = db.get_session(req.session_id) or {}
                # The anchor's called_by is ROOT
                _real_caller = "ROOT"
            except Exception:
                _real_caller = "ROOT"
        _call_token = _call_id_var.set(_real_caller)
        # Live Execution DAG streaming: poll build_exec_dag(...,
        # runtime_id) every ~1.2s while the tool runs and broadcast
        # tree_update envelopes (anchored on runtime_id) so the
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
            if _is_agentic_tool and not _in_subproc:
                # Route through a fork()'d subprocess so handle_stop's
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
                    run_agentic_in_subprocess,
                )
                import asyncio as _asyncio
                # Bridge events back. The child's wrapper will emit
                # its own placeholder + result envelopes; the ones
                # we already emitted above are anchored to the same
                # runtime_id so the second write is a no-op upsert.
                loop = _asyncio.get_event_loop()
                out = await loop.run_in_executor(
                    None,
                    lambda: run_agentic_in_subprocess(
                        tool_name=tool_name,
                        kwargs=dict(args or {}),
                        session_id=req.session_id,
                        anchor_msg_id=assistant_msg_id,
                        work_dir=None,
                        on_event=on_event,
                        # LLM-driven: pass the LLM's tool_call_id so the
                        # subprocess writes its placeholder under the
                        # SAME runtime_id the parent persisted, instead
                        # of inventing a ``forced_<random>`` and leaving
                        # us with two orphan placeholders for one call.
                        parent_call_id=call_id,
                    ),
                )
                if out.get("killed"):
                    from openprogram.agent.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.providers.types import (
                        TextContent as _CB,
                    )
                    result = _TR(content=[_CB(text="[cancelled by user]")])
                elif out.get("error"):
                    from openprogram.agent.types import (
                        AgentToolResult as _TR,
                    )
                    from openprogram.providers.types import (
                        TextContent as _CB,
                    )
                    result = _TR(content=[_CB(text=str(out["error"]))])
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
            try:
                _call_id_var.reset(_call_token)
            except Exception:
                pass
            if _live_ctx is not None:
                try:
                    _live_ctx.__exit__(None, None, None)
                except Exception:
                    pass

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
            pass
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
                             and n.called_by == runtime_id]
                _dbg_total = len(_dbg_nodes)
                _dbg_top_id = _dbg_kids[-1].id if _dbg_kids else None
                _dbg_grand = sum(1 for n in _dbg_nodes
                                 if n.called_by == _dbg_top_id) if _dbg_top_id else 0
                print(f"[dispatcher.debug] LLM-called finalize tool={tool_name} "
                      f"runtime_id={runtime_id} total_nodes={_dbg_total} "
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
        # No placeholder to update — the code node written by
        # @agentic_function is the canonical record.
        on_event({
            "type": "chat_response",
            "data": {
                "type": "result",
                "session_id": req.session_id,
                "msg_id": runtime_id,
                "content": text_out,
                "function": tool_name,
                "display": "runtime",
                "context_tree": tree_dict,
                "parent_id": assistant_msg_id,
                "timestamp": done_at,
            },
        })
        return result

    wrapped = _AgentTool(
        name=agent_tool.name,
        description=agent_tool.description,
        parameters=agent_tool.parameters,
        label=getattr(agent_tool, "label", agent_tool.name) or agent_tool.name,
        execute=_runtime_block_execute,
    )
    for _attr in ("_is_agentic", "_defer"):
        try:
            setattr(wrapped, _attr, getattr(agent_tool, _attr, None))
        except Exception:
            pass
    return wrapped
