"""REST chat entry points (parallel to the WS chat action).

Two handlers:
  POST /api/chat/branch — fork a conv at a specific message
  POST /api/function/{name} — directly run an @agentic_function via the
      forced-tool-call dispatch path (same code path as an LLM-issued
      tool call; see dispatcher.dispatch_forced_tool_call).

Sending a chat message goes through the WS ``chat`` action
(ws_actions/chat.py) — that path owns the two-stage session naming via
finalize_turn → _maybe_auto_title.
"""
from __future__ import annotations

import threading
import time
import uuid
import os

from fastapi.responses import JSONResponse
from openprogram.agent.internals._workdir import project_workdir_for
from openprogram.paths import get_default_workdir


_FUNCTION_BODY_CONTROL_KEYS = {
    "kwargs",
    "session_id",
    "_session_id",
    "project_id",
    "response_format",
}


def _resolve_work_dir(session_id: str | None = None) -> str:
    project_dir = project_workdir_for(session_id or "")
    work_dir = project_dir if project_dir is not None else get_default_workdir()
    return os.path.abspath(os.path.expanduser(str(work_dir)))


def register(app):
    @app.post("/api/chat/branch")
    async def post_chat_branch(body: dict = None):
        """Fork a conversation at a specific message — in place.

        New model: fork = move HEAD to the pivot in the same session.
        The next user turn from there writes a sibling, forking the
        DAG naturally. No new session, no history copy. Same backend
        op as ``/api/chat/checkout`` (the sibling navigator); kept as a
        separate endpoint for back-compat with older clients.
        """
        from openprogram.webui import server as _s
        if body is None:
            return JSONResponse(content={"error": "no body"}, status_code=400)
        session_id = body.get("session_id")
        pivot_id = body.get("msg_id")
        if not session_id or not pivot_id:
            return JSONResponse(
                content={"error": "session_id and msg_id required"}, status_code=400,
            )

        from openprogram.agent.session_db import default_db
        db = default_db()
        if not db.message_exists(session_id, pivot_id):
            return JSONResponse(content={"error": "unknown msg"}, status_code=404)
        db.set_head(session_id, pivot_id)
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            if conv is not None:
                conv["head_id"] = pivot_id
                try:
                    conv["messages"] = db.get_branch(session_id) or []
                except Exception:
                    pass
        _s._invalidate_messages(session_id)
        _s._save_session(session_id)
        return JSONResponse(content={
            "session_id": session_id,
            "head_id": pivot_id,
        })

    async def _set_archived(body: dict | None, archived: bool):
        """Shared body of the archive / unarchive endpoints.

        Metadata only: nothing is deleted and ``updated_at`` is left
        alone, so the session keeps its place in the list and comes
        back unchanged on unarchive. Mirrors the flag onto the live
        conv and broadcasts ``session_updated``, exactly like the WS
        ``update_session_flags`` action, so open tabs agree.
        """
        import json as _json
        from openprogram.webui import server as _s
        from openprogram.agent.session_db import default_db

        session_id = (body or {}).get("session_id")
        if not session_id:
            return JSONResponse(
                content={"error": "session_id required"}, status_code=400,
            )
        if not default_db().set_archived(session_id, archived):
            return JSONResponse(content={"error": "unknown session"}, status_code=404)
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            if conv is not None:
                conv["archived"] = archived
        _s._broadcast(_json.dumps({
            "type": "session_updated",
            "data": {"id": session_id, "archived": archived},
        }, default=str))
        return JSONResponse(content={"session_id": session_id, "archived": archived})

    @app.post("/api/sessions/archive")
    async def post_session_archive(body: dict = None):
        """Hide a session from the default list. Reversible, deletes nothing."""
        return await _set_archived(body, True)

    @app.post("/api/sessions/unarchive")
    async def post_session_unarchive(body: dict = None):
        """Return an archived session to the default list."""
        return await _set_archived(body, False)

    @app.post("/api/function/{name}")
    async def post_function(name: str, body: dict = None):
        """Directly invoke an @agentic_function through the forced
        tool-call dispatch path. Replaces the former ``/api/run`` —
        all @agentic_function runs (UI-triggered or LLM-issued) now
        share ``dispatcher._wrap_agentic_runtime_block``.

        Body:
          ``session_id`` (optional) — target conversation; created if absent.
          ``kwargs`` (dict)         — function arguments.
          ``project_id`` (str, optional) — pending Project selection for a
            new fn-form session; bound before the function starts.
        """
        body = body or {}
        response_format = None
        if body.get("response_format") is not None:
            from openprogram.providers.structured_output import (
                StructuredOutputError,
                normalize_response_format,
            )
            try:
                response_format = normalize_response_format(body["response_format"])
            except StructuredOutputError as exc:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Structured output request is invalid",
                        "code": exc.code,
                        "issues": exc.issues,
                    },
                )
        session_id = body.get("session_id") or body.get("_session_id")
        if isinstance(body.get("kwargs"), dict):
            kwargs = dict(body.get("kwargs") or {})
        else:
            # Compatibility for older callers that posted function
            # params at the top level instead of under ``kwargs``.
            kwargs = {
                k: v
                for k, v in body.items()
                if k not in _FUNCTION_BODY_CONTROL_KEYS
            }
        project_id = body.get("project_id")
        # ``fork_of_node``: edit-and-rerun. Anchor the run at the named
        # prior call's predecessor so it lands as a SIBLING branch of that
        # run (same fork model as retry_function), not a stacked new call.
        anchor = None
        fork_of = body.get("fork_of_node")
        if fork_of and session_id:
            from openprogram.agent.session_db import default_db
            from openprogram.webui.ws_actions.chat import _call_predecessor
            try:
                nodes = default_db().get_nodes(session_id)
            except Exception:
                nodes = []
            node = next((n for n in nodes if n.id == fork_of), None)
            if node is not None:
                anchor = _call_predecessor(node)
        dispatch_options = {
            "anchor_msg_id": anchor,
            "response_format": response_format,
        }
        if isinstance(project_id, str) and project_id:
            dispatch_options["project_id"] = project_id
        result = run_agentic_function_call(
            name,
            kwargs,
            session_id,
            **dispatch_options,
        )
        if "error" in result:
            return JSONResponse(status_code=result.pop("status_code", 400),
                                content=result)
        return JSONResponse(content=result)


def apply_user_goal_context_mode(name: str, kwargs: dict) -> dict:
    """User-forced goal (form / slash / welcome / retry) defaults to session."""
    if name == "goal" and "context_mode" not in kwargs:
        kwargs["context_mode"] = "session"
    return kwargs


def run_agentic_function_call(
    name: str,
    kwargs: dict,
    session_id: str | None = None,
    anchor_msg_id: str | None = None,
    response_format=None,
    project_id: str | None = None,
) -> dict:
    """Dispatch an @agentic_function via the forced tool-call path and
    return ``{"session_id", "msg_id"}`` (or ``{"error", "status_code",
    ...}`` on a validation failure).

    Shared by ``POST /api/function/{name}`` (fn-form / welcome button)
    and the WS ``retry_function`` action (the Retry button) so both go
    through one code path — a top-level code node appended to the session
    DAG, dispatched exactly like an LLM-issued tool call.

    ``anchor_msg_id`` controls where the run lands on the conversation
    chain — function calls sit on the same chain chat turns use:

    * ``None`` (default — a NEW run from fn-form / welcome) → passed as an
      EMPTY caller, which makes the @agentic_function decorator stamp the
      run's ``predecessor`` with the session's CURRENT HEAD (see
      ``function.py`` — the "top-level manual call" branch). The run
      chains SEQUENTIALLY off the previous turn's terminal node, exactly
      like a new chat turn: distinct predecessor → its own 1/1 card, no
      false siblings. An empty session (no head) → a root-level run.
    * explicit id (the Retry button passes the ORIGINAL call's
      predecessor) → becomes the re-run's caller so it lands as a SIBLING
      of that call (same fork model as chat-message retry): both runs
      share the original's predecessor, so the version switcher counts
      2/2 and only the active head renders in the transcript.

    The forced path advances HEAD to the new node, so the newest run
    becomes the active branch and only it renders in the transcript.
    """
    from openprogram.webui import server as _s

    # Synchronously validate the tool exists AND is @agentic_function
    # BEFORE we create a session / write a user msg / spawn the
    # subprocess. Without this gate, picking a non-agentic function
    # in fn-form would land in dispatch_forced_tool_call's raise
    # path inside a daemon thread; the HTTP response had already
    # returned 200 with a session_id + msg_id, so the frontend
    # showed a phantom "[function call] foo()" user row that never
    # produced output. Reject early so the caller sees the reason
    # in the response body.
    try:
        from openprogram.programs._runtime import get as _get_tool
        _tool = _get_tool(name)
    except Exception as e:  # noqa: BLE001
        return {"error": f"failed to resolve tool {name!r}: {type(e).__name__}: {e}",
                "status_code": 500}
    if _tool is None:
        return {"error": f"tool not found: {name!r}", "status_code": 404}
    if not getattr(_tool, "_is_agentic", False):
        return {
            "error": (
                f"tool {name!r} is not an @agentic_function — "
                "only agentic tools can be invoked via fn-form. "
                "Use the chat interface or LLM tool-call path "
                "for ordinary tools."
            ),
            "tool": name,
            "is_agentic": False,
            "status_code": 400,
        }

    # No enabled model → refuse. An agentic function still needs a
    # model to dispatch its agent loop against; with everything
    # disabled the run would fall back to a pinned / auto-detected
    # default the user explicitly turned off. Reject so the UI can
    # prompt for a model instead of silently executing (the exact
    # surprise of "I disabled everything yet gui_agent still ran").
    if not _s._runtime_management._enabled_model_keys():
        return {
            "error": (
                "No model enabled. Enable a model in "
                "Settings → Providers before running a function."
            ),
            "code": "no_model",
            "status_code": 409,
        }

    kwargs = apply_user_goal_context_mode(name, dict(kwargs or {}))
    conv = _s._get_or_create_session(session_id)
    session_id = conv["id"]
    if project_id:
        from openprogram.agent.session_db import default_db
        from openprogram.store.project import project_store as _projects

        if _projects.get_project(project_id) is None:
            return {
                "error": f"unknown project: {project_id!r}",
                "code": "unknown_project",
                "status_code": 400,
            }
        default_db().update_session(session_id, project_id=project_id)
        _projects.bind_session(session_id, project_id)
    msg_id = uuid.uuid4().hex[:8]
    # Claim the same atomic session occupancy used by chat before mutating
    # the DAG. The reservation covers parent-side node creation; activation
    # below hands ownership to the direct function worker.
    if not _s._try_reserve_run(session_id, msg_id):
        return {
            "error": _s.RUN_ACTIVE_ERROR,
            "code": "run_active",
            "status_code": 409,
        }
    try:
        provider, model = _s._runtime_management._resolve_session_provider_model(conv)
    except Exception as exc:
        _s._release_run_reservation(session_id, msg_id)
        return {
            "error": f"failed to resolve the session model: {type(exc).__name__}: {exc}",
            "code": "model_resolution_failed",
            "status_code": 500,
        }
    if not provider:
        _s._release_run_reservation(session_id, msg_id)
        return {
            "error": (
                "No model available for this session. Enable a model or "
                "select one before running a function."
            ),
            "code": "no_model",
            "status_code": 409,
        }
    # A NEW run (anchor left unset) passes an EMPTY caller so the
    # @agentic_function decorator stamps its the predecessor field with the
    # session's current head (function.py's top-level-call branch) — the
    # run chains off the previous turn's terminal node like a new chat
    # turn. An explicit anchor (the Retry button) is honoured verbatim as
    # the run's caller so it forks as a sibling of the original.
    if anchor_msg_id is None:
        anchor_msg_id = ""
    try:
        work_dir = _resolve_work_dir(session_id)
        from openprogram.agent.session_db import default_db as _rc_db2
        agent_id = (
            (_rc_db2().get_session(session_id) or {}).get("agent_id")
            or _s._default_agent_id()
        )
    except Exception as exc:
        _s._release_run_reservation(session_id, msg_id)
        return {
            "error": f"failed to prepare function execution: {type(exc).__name__}: {exc}",
            "code": "function_setup_failed",
            "status_code": 500,
        }
    # msg_id is only a WS-routing handle for the response stream; it is
    # never written to the DAG. The code node written by the
    # @agentic_function is the canonical record: a NEW run (empty anchor)
    # gets the predecessor field = the session head (or ROOT for an empty
    # session); a Retry (explicit pred:<id> anchor) forks off that id.
    # Ensure the session ROOT node exists so a run that anchors at ROOT
    # (empty session, or a legacy retry) resolves to a real node. No
    # anchor row is written for the run itself.
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.context.nodes import Call as _C, ROLE_USER as _RU
        from openprogram.store import SessionNodeWriter as _GS
        _db = default_db()
        if not _db.message_exists(session_id, "ROOT"):
            _GS(_db, session_id).append(_C(
                id="ROOT", role=_RU, output="",
                metadata={"display": "root"},
            ))
    except Exception:
        pass

    # PRE-CREATE the run's top-level code node in THIS (parent) process,
    # before spawning the child, so head advances and the pending "running"
    # card lands on disk within milliseconds of the WS action — instead of
    # waiting ~1s for the spawned child's fresh-interpreter import to run
    # before its wrapper appends the node. The child REUSES this id
    # (threaded across the spawn as a ``|node:<id>`` anchor suffix) and its
    # append no-ops, so head / predecessor are stamped exactly once here.
    #
    # ``anchor_msg_id`` encodes the fork point: a retry passes ``pred:<id>``
    # (fork off that id, empty caller); fn-form passes ``""`` (chain off the
    # session head). Mirror the @agentic_function wrapper's resolution so
    # the pre-created node is byte-identical to what the child would write.
    _forced_node_id = None
    _pending_nid = uuid.uuid4().hex[:12]
    _precreate_error = None
    for _attempt in range(2):
        try:
            from openprogram.agentic_programming.function import (
                create_pending_call_node as _mk_node,
                _registry as _fn_registry,
            )
            from openprogram.store import SessionNodeWriter as _GS2
            _inst = _fn_registry.get(name) or next(
                (v for v in _fn_registry.values()
                 if getattr(v, "tool_name", None) == name), None,
            )
            _expose = getattr(_inst, "expose", "io") if _inst else "io"
            _hidden = _expose == "hidden"
            _caller = ""
            _forced_pred = None
            if isinstance(anchor_msg_id, str) and anchor_msg_id.startswith("pred:"):
                _forced_pred = anchor_msg_id[len("pred:"):]
            elif anchor_msg_id:
                _caller = anchor_msg_id
            _shim = _GS2(default_db(), session_id)
            _node = _mk_node(
                pending_id=_pending_nid,
                function_name=name,
                arguments={} if _hidden else kwargs,
                expose="io" if _hidden else _expose,
                render_range=(
                    None if _hidden
                    else getattr(_inst, "render_range", None) if _inst else None
                ),
                docstring=(
                    "" if _hidden
                    else (getattr(getattr(_inst, "_fn", None), "__doc__", "") or "").strip()
                    if _inst else ""
                ),
                caller=_caller,
                forced_predecessor=_forced_pred,
                store=_shim,
            )
            if _node is not None:
                if _hidden:
                    _node.input = None
                    _node.metadata.update({
                        "expose": "hidden",
                        "execution_control": True,
                    })
                _shim.append(_node)
                _forced_node_id = _pending_nid
                # Thread the pre-created id to the child so its wrapper
                # reuses it instead of appending a duplicate top-level node.
                anchor_msg_id = f"{anchor_msg_id}|node:{_pending_nid}"
            _precreate_error = None
            break
        except Exception as exc:
            _precreate_error = exc
    if _precreate_error is not None:
        _s._release_run_reservation(session_id, msg_id)
        return {
            "error": "failed to persist the execution record",
            "code": "execution_record_failed",
            "status_code": 500,
        }

    execution_id = _forced_node_id or f"{msg_id}_reply"
    with _s._running_tasks_lock:
        _reserved_task = _s._running_tasks.get(session_id)
        if _reserved_task and _reserved_task.get("msg_id") == msg_id:
            _reserved_task.update({
                "func_name": name,
                "execution_id": execution_id,
            })

    # Stage-1 title (immediate placeholder): the call signature, so
    # the sidebar row shows instantly and the session survives a
    # refresh — without an anchor user row there is no preview, and
    # build_sessions_list drops title-less + preview-less rows.
    #
    # Per docs/design/runtime/session/, fn-form takes the SAME
    # two-stage naming as a normal chat: this signature is the
    # stage-1 truncation, and stage-2 is the background LLM rename
    # (below, after the call produces a result). No lock flag is set
    # here, so stage-2 is free to rename it — fn-form is not pinned.
    _fn_title = ""
    try:
        _arg_bits = "" if _hidden else ", ".join(
            f"{k}={v!r}" if not isinstance(v, str) or len(v) <= 40
            else f"{k}={v[:37]!r}…"
            for k, v in (kwargs or {}).items()
        )
        _fn_title = f"{name}({_arg_bits})"[:80]
        _existing = _rc_db2().get_session(session_id) or {}
        _meta_fields = {"title": _fn_title, "agent_id": agent_id}
        # Stamp created_at on first use so the row sorts to the top of
        # the sidebar (build_sessions_list orders by created_at desc).
        if not _existing.get("created_at"):
            _meta_fields["created_at"] = time.time()
        _rc_db2().update_session(session_id, **_meta_fields)
    except Exception:
        pass

    def _run():
        from openprogram.agent.dispatcher import dispatch_forced_tool_call
        try:
            try:
                out = dispatch_forced_tool_call(
                    session_id=session_id,
                    anchor_msg_id=anchor_msg_id,
                    tool_name=name,
                    tool_input=kwargs,
                    work_dir=work_dir,
                    agent_id=agent_id,
                    source="fn-form",
                    provider=provider,
                    model=model,
                    response_format=response_format,
                    execution_id=_forced_node_id,
                    on_event=lambda env: _s._broadcast_envelope(env)
                        if hasattr(_s, "_broadcast_envelope")
                        else _s._broadcast(__import__("json").dumps(env, default=str)),
                )
            except Exception as e:  # noqa: BLE001
                if _forced_node_id:
                    from openprogram.agent.run_control import mark_execution_terminal
                    mark_execution_terminal(_forced_node_id, "error")
                _s._broadcast_chat_response(session_id, msg_id, {
                    "type": "error",
                    "content": f"function call failed: {type(e).__name__}: {e}",
                    "function": name,
                    "display": "runtime",
                })
                return
            if (out or {}).get("ok") and _fn_title:
                # Stage-2 of the doc's two-stage naming: the function has
                # produced a result, so let the LLM rename the session
                # over the call + output (race-guarded; never locks).
                from openprogram.agent.dispatcher.titles import (
                    fn_form_llm_title,
                )
                fn_form_llm_title(_rc_db2(), session_id, _fn_title)
        finally:
            # The function run is over (success / error / exception) —
            # clear the running task so the sidebar's flowing animation
            # stops and the composer's stop button reverts to send.
            # Without this the session shows "running" forever (the
            # chat path clears via _execute finalize; the fn-form path
            # never did — only set it on start). Mirrors
            # _execute/chat.py:277-278.
            try:
                if _s._finish_owned_run(session_id, msg_id):
                    _s._emit_running_task_event(
                        session_id,
                        cleared_msg_id=msg_id,
                        cleared_execution_id=execution_id,
                    )
            except Exception:
                pass

    try:
        worker = threading.Thread(target=_run, daemon=True)
    except BaseException as exc:
        _s._release_run_reservation(session_id, msg_id)
        if _forced_node_id:
            from openprogram.agent.run_control import mark_execution_terminal
            mark_execution_terminal(_forced_node_id, "error")
        return {
            "error": f"failed to create function execution: {type(exc).__name__}: {exc}",
            "code": "function_start_failed",
            "status_code": 500,
        }

    if not _s._activate_run_reservation(session_id, msg_id, worker):
        if _forced_node_id:
            from openprogram.agent.run_control import mark_execution_terminal
            mark_execution_terminal(_forced_node_id, "error")
        return {
            "error": "function execution reservation was lost before startup",
            "code": "function_start_failed",
            "status_code": 409,
        }
    _s._emit_running_task_event(session_id)

    try:
        worker.start()
    except BaseException as exc:  # thread creation/start must roll back occupancy
        if _forced_node_id:
            try:
                from openprogram.agent.run_control import mark_execution_terminal
                mark_execution_terminal(_forced_node_id, "error")
            except Exception:
                pass
        if _s._finish_owned_run(session_id, msg_id):
            _s._emit_running_task_event(
                session_id,
                cleared_msg_id=msg_id,
                cleared_execution_id=execution_id,
            )
        return {
            "error": f"failed to start function execution: {type(exc).__name__}: {exc}",
            "code": "function_start_failed",
            "status_code": 500,
        }

    # The fn-form path creates the session row directly (no WS
    # action ran), so the sidebar — which only fetches the list on
    # mount/manual refresh — never learns about the new session.
    # Broadcast the current list once so every connected client's
    # sidebar shows the new conversation immediately.
    try:
        from openprogram.webui.ws_actions.session import broadcast_sessions_list
        broadcast_sessions_list()
    except Exception:
        pass

    return {
        "session_id": session_id,
        "msg_id": msg_id,
        "execution_id": _forced_node_id,
    }
