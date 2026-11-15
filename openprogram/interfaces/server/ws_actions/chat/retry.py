"""The ``retry_function`` action: re-running a selected function call."""
from __future__ import annotations

import asyncio
import json
import uuid


def _retry_call_node(session_id: str, node_id: str, func_name: str):
    """Return the exact persisted top-level code node named by Retry."""
    if not all(
        isinstance(value, str) and value
        for value in (session_id, node_id, func_name)
    ):
        return None
    from openprogram.runtime.context.session_db import default_db
    try:
        nodes = default_db().get_nodes(session_id)
    except Exception:
        return None
    code_ids = {n.id for n in nodes if n.is_code()}
    return next((
        node for node in nodes
        if node.id == node_id
        and node.is_code()
        and node.name == func_name
        and isinstance(node.input, dict)
        and node.caller not in code_ids
    ), None)


def _call_predecessor(node) -> str:
    """The anchor a re-run passes so it lands as a SIBLING of ``node``
    (same fork point). Returned as ``pred:<id>`` — the forced-tool path
    decodes that into the re-run's ``predecessor`` (with an empty
    caller), matching the edge a fresh chained run uses, so the two runs
    are true alternatives sharing one predecessor.

    The fork point is ``node``'s own conversation predecessor (mirrors
    chat-retry's ``predecessor = src.predecessor``), falling back to the
    node's caller, then "ROOT" — so a first/root-level run re-runs as a
    ROOT sibling and an LLM-issued call re-runs off the same reply it
    originally hung from.

    ``predecessor`` is a top-level ``Call`` field (dag/overview.md §3);
    it is popped out of metadata on every write path, so reading
    ``metadata["predecessor"]`` here always yielded None and silently
    collapsed every top-level re-run onto "ROOT"."""
    pred = getattr(node, "predecessor", None)
    fork = pred or getattr(node, "caller", None) or "ROOT"
    return f"pred:{fork}"


async def handle_retry_function(ws, cmd: dict):
    """Re-run the exact function code node selected by the user.

    Wired to the runtime-block Retry button. Mirrors chat-message retry
    (``_fork_user_turn_and_run``): the re-run is anchored at the original
    call's OWN predecessor, so it forks off the same point rather than
    stacking as a second sequential node. The forced-tool-call path
    advances HEAD to the new node, so the retried run becomes the active
    branch — only it renders in the transcript, and the old run is
    reachable via the runtime-block's version switcher (< N/M >) and the
    Branches panel. Old messages are never stripped.
    """
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.routes.chat import run_agent_method_call

    session_id = cmd.get("session_id")
    func_name = cmd.get("function")
    if not session_id or not func_name:
        return

    def _fail(message: str) -> None:
        _s._broadcast_chat_response(session_id, uuid.uuid4().hex, {
            "type": "error",
            "content": f"Retry failed: {message}",
            "function": func_name,
            "display": "runtime",
            "retry_request_id": cmd.get("retry_request_id"),
        })

    # Every store read and the run's admission take the conversation's
    # lock: off the event loop, so a held conversation delays this only.
    node = await asyncio.to_thread(_retry_call_node, session_id, cmd.get("node_id"), func_name)
    if node is None:
        _fail(f"no matching {func_name!r} call node found in this session.")
        return

    kwargs = {k: v for k, v in node.input.items()
              if k not in ("runtime", "callback")}
    # Anchor the re-run at the ORIGINAL call's predecessor so it lands as
    # a sibling branch (same fork model as chat retry), not a stacked run.
    anchor = _call_predecessor(node)

    from openprogram.interfaces.server.ws_actions import webtab
    registered_window_id = next((
        window_id for owner, window_id, _revision
        in webtab.registered_desktop_windows()
        if owner is ws
    ), None)
    clicked_surface = (
        cmd.get("surface_ref")
        if isinstance(cmd.get("surface_ref"), dict) else None
    )
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    if "surface_origin" in metadata:
        stored_origin = metadata["surface_origin"]
        stored_version = (
            stored_origin.get("version")
            if isinstance(stored_origin, dict) else None
        )
        stored_window = (
            stored_origin.get("window_id")
            if isinstance(stored_origin, dict) else None
        )
        stored_tab = (
            stored_origin.get("tab_id")
            if isinstance(stored_origin, dict)
            and "tab_id" in stored_origin else None
        )
        stored_version_valid = (
            type(stored_version) is int and stored_version == 1
        )
        stored_window_valid = (
            isinstance(stored_window, str) and bool(stored_window)
        )
        stored_tab_valid = (
            stored_tab is None
            or (isinstance(stored_tab, str) and bool(stored_tab))
        )
        if not (
            stored_version_valid and stored_window_valid and stored_tab_valid
        ):
            _fail("the selected call has an invalid stored Page origin.")
            return
        if registered_window_id != stored_window:
            _fail("the original desktop window is no longer connected here.")
            return
        origin_window_id = stored_window
        surface_ref = (
            {
                "version": 1,
                "window_id": stored_window,
                "tab_id": stored_tab,
            }
            if stored_tab else None
        )
    else:
        # Nodes created before surface_origin was persisted may use the
        # exact Page reported by the desktop at click time.
        origin_window_id = registered_window_id
        surface_ref = clicked_surface
        surface_window_id = (
            surface_ref.get("window_id")
            if isinstance(surface_ref, dict)
            and isinstance(surface_ref.get("window_id"), str)
            else None
        )
        if surface_ref and (
            not origin_window_id or surface_window_id != origin_window_id
        ):
            _fail("surface belongs to another desktop window.")
            return
    options = {"anchor_msg_id": anchor, "retry_of": node.id}
    if origin_window_id:
        options["origin_window_id"] = origin_window_id
    if surface_ref:
        options["surface_ref"] = surface_ref
    from .turn_request import client_request_id_of
    request_id = client_request_id_of(cmd)
    if request_id:
        options["client_request_id"] = request_id
    result = await asyncio.to_thread(
        run_agent_method_call, func_name, kwargs, session_id, **options,
    )
    if "error" in result:
        _fail(result["error"])
        return
    from .turn import _join_and_read
    ack_session = result.get("session_id", session_id)
    first_read, end_parking = await _join_and_read(ws, ack_session)
    ack_extra = {**first_read, **({"client_request_id": request_id} if request_id else {})}
    try:
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            # ``function_run`` tells the frontend this ack is a function
            # dispatch whose top-level card was PRE-CREATED on disk at dispatch
            # time (run_agent_method_call), so it can hydrate the transcript
            # immediately instead of waiting for the first tree_update (~1.85s
            # after the spawned child's import finishes). See wsHandleChatAck.
            "data": {**ack_extra,
                     "session_id": ack_session,
                     "msg_id": result.get("msg_id", ""),
                     "execution_id": (
                         result.get("execution_id")
                         or result.get("msg_id")
                         or ""
                     ),
                     "retry_request_id": cmd.get("retry_request_id"),
                     "function_run": True},
        }))
    finally:
        end_parking()
