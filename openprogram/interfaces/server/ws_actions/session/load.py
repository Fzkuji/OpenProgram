"""Session hydration: the ``load_session`` action."""
from __future__ import annotations

import contextvars
import json
import logging

from openprogram.state.store.session.conversation_work import run_for_conversation


_log = logging.getLogger(__name__)
# The conversation a load reads: its store reads run in that conversation's lane.
_loading: contextvars.ContextVar[str | None] = contextvars.ContextVar("loading_session", default=None)

_HISTORY_PAGE_KEYS = ("history_before", "history_after", "history_around", "history_latest", "history_turn")


def _branched_from(session_row: dict) -> dict | None:
    """``{"session_id", "msg_id", "title", "exists"}`` for a branched
    session, else None.

    ``title`` is the source's title now (or "" when it has none or is
    gone); the client prefers its live session list for renames.
    ``exists`` is False once the source was deleted.
    """
    lineage = (session_row.get("extra_meta") or {}).get("branched_from") \
        or session_row.get("branched_from")
    if not isinstance(lineage, dict) or not lineage.get("session_id"):
        return None
    title, exists = "", False
    try:
        from openprogram.runtime.context.session_db import default_db
        source = default_db().get_session(str(lineage["session_id"]))
        exists = source is not None
        title = str((source or {}).get("title") or "")
    except Exception:  # noqa: BLE001 — the banner falls back to a generic name
        title, exists = "", False
    return {"session_id": lineage["session_id"], "msg_id": lineage.get("msg_id"),
            "title": title, "exists": exists}


def _session_todos(session_id: str) -> list:
    try:
        from openprogram.programs.tools.planning.todo import shared as _todos
        return _todos.load(session_id)
    except Exception:
        return []


def _session_todo_plans(session_id: str) -> list:
    try:
        from openprogram.programs.tools.planning.todo import shared as _todos
        return _todos.load_all_plans(session_id)
    except Exception:
        return []


async def _session_io(func, *args, **kwargs):
    """Run one bounded session-store read without blocking the WS loop.

    It runs in the loaded conversation's lane: loads waiting on a held
    conversation take at most that lane's threads, never the others'."""
    return await run_for_conversation(_loading.get(), func, *args, **kwargs)


def _check_page_request(cmd: dict) -> None:
    """A page read names exactly one cursor, and every cursor is a row id."""
    from openprogram.interfaces.server.ws_errors import OperationError
    if sum(key in cmd for key in _HISTORY_PAGE_KEYS) != 1:
        raise OperationError("invalid_request", scope="session")
    for key in ("history_before", "history_after", "history_around", "history_turn"):
        value = cmd.get(key)
        if value is not None and not isinstance(value, str):
            raise OperationError("invalid_request", scope="session")


async def _find_or_hydrate_session(_s, session_id):
    """The in-memory session, hydrated from SessionDB on a cold load; None when it does not exist."""
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id)
    # Cold load (fresh worker, new browser session, or a session created
    # by the fn-form REST path that never went through a WS chat turn):
    # the conv isn't in the in-memory map yet. If it exists on disk,
    # hydrate it from SessionDB so the full aggregation below runs —
    # otherwise we'd fall to the empty-stub ``else`` and the page would
    # render the Welcome screen for a session that actually has history
    # (e.g. a manually-invoked Agent method's top-level code node).
    if conv is None and session_id:
        from openprogram.runtime.context.session_db import default_db as _db_probe
        try:
            _exists = await _session_io(
                lambda: _db_probe().get_session(session_id) is not None
            )
        except Exception:
            _exists = False
        if _exists:
            conv = await _session_io(
                _s._get_or_create_session, session_id
            )
    return conv


def _read_failed(session_id, exc: Exception):
    """The retryable error for a read that failed; the client keeps its rows and offers a retry."""
    from openprogram.interfaces.server.ws_errors import OperationError
    _log.warning("[load_session] read of %s failed: %r", session_id, exc)
    return OperationError("session_read_failed", scope="session", retryable=True)


def _context_stats_for_load(conv: dict):
    """Current-version context stats with the cached breakdown folded in: ``(stats, refresh_after_load)``."""
    _stats = conv.get("_last_context_stats") or {}
    if _stats.get("context_usage_version") != 2:
        conv.pop("_last_context_stats", None)
        conv.pop("_last_context_breakdown", None)
        conv.pop("_chat_usage", None)
        _stats = {}
    refresh_context_after_load = not conv.get("_last_context_breakdown")
    _bd = conv.get("_last_context_breakdown")
    # The window belongs to the model, not to the saved record: resolve it
    # now, so a record saved with a wrong window (the old 128k default)
    # shows the model's real one, or none when the model is unknown.
    from openprogram.interfaces.server.server_runtime.context_stats import with_window
    from openprogram.interfaces.server import server as _s
    window = _s._conv_context_window(conv)
    if _stats:
        _stats = with_window(_stats, window)
        if isinstance(_stats.get("breakdown"), dict):
            _stats["breakdown"] = with_window(_stats["breakdown"], window)
        conv["_last_context_stats"] = _stats
    if _bd:
        _bd = conv["_last_context_breakdown"] = with_window(_bd, window)
    if _bd and "breakdown" not in _stats:
        _stats = {**_stats, "type": "context_stats", "breakdown": _bd}
        conv["_last_context_stats"] = _stats
    return _stats, refresh_context_after_load


class _SelectionChanged(Exception):
    """A read raced an explicit branch selection; retry its entire snapshot."""


def _load_selection(db, session_id):
    row = db.ensure_branch_config(session_id) if hasattr(db, "ensure_branch_config") else db.get_session(session_id)
    row = row or {}
    return row.get("active_branch_id"), row.get("head_version", 0)


def _capture_load_settings(db, session_id, selection, head):
    from contextlib import nullcontext
    from copy import deepcopy
    pair = db._open(session_id) if hasattr(db, "_open") else None
    lock = db._head_file_lock(pair[0]) if pair else nullcontext()
    with lock:
        row = db.get_session(session_id) or {}
        if selection is not None:
            identity, version = selection
            if row.get("active_branch_id") != identity or (
                row.get("head_version", 0) != version and row.get("head_id") != head
            ):
                raise _SelectionChanged
        return deepcopy(row)


def _resolved_settings(session_id: str, branch_id) -> dict:
    from openprogram.interfaces.server.branch_settings import resolved_settings
    try:
        return resolved_settings(session_id, branch_id)
    except Exception:  # noqa: BLE001 - the controls show the stored values instead
        return {}


async def _send_session_loaded(ws, _s, cmd: dict, conv: dict, session_id, transcript: dict, head, spawned_branch, selection=None):
    """Send the ``session_loaded`` frame: ``transcript``'s fields, settings and run state; returns ``(run_task, refresh_context_after_load)``.

    Every read behind the frame takes the conversation's lock, so the
    frame is built in a worker thread: a conversation held by a turn or a
    cold load delays this read only, never the event loop."""
    data, run_task, refresh_context_after_load = await _session_io(
        _session_loaded_data, _s, cmd, conv, session_id, transcript, head, spawned_branch, selection,
    )
    await ws.send_text(json.dumps({"type": "session_loaded", "data": data},
                                  ensure_ascii=False, default=str))
    return run_task, refresh_context_after_load


def _session_loaded_data(_s, cmd: dict, conv: dict, session_id, transcript: dict, head, spawned_branch, selection):
    """The ``session_loaded`` frame's data, ``run_task`` and ``refresh_context_after_load``."""
    from openprogram.runtime.context.session_db import default_db as _ddb
    from openprogram.runtime.conversation.session_settings.session_config import (
        _config_from_row,
        permission_from_config,
        project_defaults,
    )
    from openprogram.host.sandbox import ui_state as _sandbox_ui
    db = _ddb()
    _db_sess = _capture_load_settings(db, session_id, selection, head)
    run_cfg = _config_from_row(_db_sess)
    from openprogram.runtime.permissions.state import permission_state_from_row
    from openprogram.interfaces.server.runtime_management import _provider_info_from_snapshot
    _permission_state = permission_state_from_row(session_id, _db_sess)
    _effective_permission = permission_from_config(
        run_cfg, default=project_defaults(conv["id"]).get("permission_mode"))
    _run_seq = _s._next_run_seq()
    # ``run_active`` / ``run_task`` keep meaning the selected branch's turn;
    # ``running_branches`` names every running turn with its branch.
    _run_active = _s._is_selected_run_active(conv["id"])
    _run_task = _s._run_identity(conv["id"]) if _run_active else None
    _running_branches = _s._running_branches(conv["id"])
    _stats, refresh_context_after_load = _context_stats_for_load(conv)
    from openprogram.programs.tools.planning.goal.presentation import history_projection
    tree_data = {}  # tree Context retired — execution trace lives in SessionDB DAG nodes
    data = {
        "request_id": cmd.get("request_id"),
        "action": "load_session",
        "id": conv["id"],
        "title": _db_sess.get("title", ""),
        "agent_id": _db_sess.get("agent_id"),
        "active_branch_id": _db_sess.get("active_branch_id"),
        # The branch a person's message last went to.
        "last_branch_id": _db_sess.get("last_branch_id"),
        **transcript,
        # The DAG panel reads the graph from branches_list.
        "graph": [],
        "head_id": head,
        "spawned_branch": spawned_branch,
        # Lineage of a conversation made with "Copy conversation":
        # the transcript's "Copied from" banner.
        "branched_from": _branched_from(_db_sess),
        "context_tree": tree_data,
        "provider_info": _provider_info_from_snapshot(_db_sess),
        "context_stats": _stats or None,
        "channel": _db_sess.get("channel"),
        "account_id": _db_sess.get("account_id"),
        "environment_id": _db_sess.get("environment_id"),
        "peer": _db_sess.get("peer"),
        "peer_display": _db_sess.get("peer_display"),
        "source": _db_sess.get("source"),
        # Session goal (/goal) — the composer's GoalChip hydrates
        # from this on load; live changes ride goal_update frames.
        "goal": history_projection((_db_sess.get("extra_meta") or {}).get("goal"), session_id),
        # The task lists drawn under the turns that changed the plan
        # hydrate from these; live changes ride todos_update frames.
        "todos": _session_todos(session_id),
        "todo_plans": _session_todo_plans(session_id),
        "settings": {
            "tools_override": run_cfg.tools_override,
            "web_search": run_cfg.web_search,
            "service_tier": _db_sess.get("service_tier"),
            "thinking_effort": run_cfg.thinking_effort,
            "permission_mode": _effective_permission,
            "permission_version": _permission_state["version"],
            "additional_working_dirs": run_cfg.additional_working_dirs,
            **_sandbox_ui(run_cfg.sandbox_enabled),
            # Each setting as it resolves, where from, and what the
            # branch has when it sets nothing (the menus' "Agent default").
            "resolved": _resolved_settings(session_id, _db_sess.get("active_branch_id")),
        },
        "run_active": _run_active,
        "run_task": _run_task,
        "running_branches": _running_branches,
        "run_seq": _run_seq,
        "status": (_ddb().get_session(session_id) or {}).get("status", "idle"),
    }
    return data, _run_task, refresh_context_after_load


def _running_task_snapshot(_s, session_id):
    """``(run_seq, slot, task)``: the selected branch's slot and its running
    task, the durable record first (it reads the conversation's lock)."""
    _followup_seq = _s._next_run_seq()
    with _s._running_tasks_lock:
        task = _s._running_tasks.get(session_id)
        # A reservation's release always sends the clear that names it, so
        # this client can hold it as its running task.
        slot_info = dict(task) if task else None
    return _followup_seq, slot_info, _s._reported_run_task(session_id, slot_info)


async def _send_running_task_state(ws, _s, session_id, _run_task) -> None:
    """Follow up with the live running task, or clear the slot of a turn that ended during the load."""
    # The run guard owns stale runtime and reservation cleanup. A quiet
    # live task must retain its execution identity for reconnect controls.
    _followup_seq, slot_info, task_info = await _session_io(_running_task_snapshot, _s, session_id)
    if slot_info and task_info is not slot_info and slot_info.get("msg_id") == task_info.get("msg_id"):
        # The turn of a retry, an edit or a function run keeps its operation's kind and id.
        task_info = {**task_info, **{key: slot_info[key] for key in ("op_id", "op_kind", "op_version")
                                     if key in slot_info}}
    if _run_task and not task_info:
        # The turn this load reported ended while the load was in
        # flight; its broadcast clear may have reached the client before
        # the running slot did, so close that slot by name here.
        await ws.send_text(json.dumps({
            "type": "running_task_clear",
            "data": {"session_id": session_id, "run_seq": _followup_seq, **{
                key: value for key, value in _run_task.items()
                if value and key in ("msg_id", "execution_id", "op_id")}},
        }))
    if task_info:
        from openprogram.interfaces.server.server_runtime.session_persistence import reply_row_id
        from openprogram.interfaces.server.server_runtime.session_lifecycle import operation_identity

        identity = operation_identity(task_info)
        # Live partial-tree dump retired with the tree-Context
        # event system. The DAG nodes the function has produced so
        # far are already queryable via the GraphStore.
        await ws.send_text(json.dumps({
            "type": "running_task",
            "data": {
                "session_id": session_id,
                "msg_id": task_info["msg_id"],
                "reply_id": reply_row_id(task_info),
                "func_name": task_info["func_name"],
                "execution_id": task_info.get("execution_id"),
                "status_version": identity.get("status_version", task_info.get("status_version")),
                # What the operation is (a reply, /compact, a rewind ...) and
                # the id a Stop of an operation with no execution names.
                "op_kind": identity["op_kind"],
                "op_id": identity.get("op_id"),
                "started_at": task_info["started_at"],
                "display_params": task_info.get("display_params", ""),
                "partial_tree": None,
                "stream_events": task_info.get("stream_events", []),
                "status": task_info.get("status"),
                "reason_code": task_info.get("reason_code"),
                "run_seq": _followup_seq,
            },
        }, default=str))


async def _replay_open_waits(ws, _s, session_id) -> None:
    """Resend the question and system-access frames of waits still open for this session."""
    # Reconnect recovery (user-input-requests.md): a function may be
    # blocked in runtime.ask right now. The live ``question.asked`` frame
    # already fired before this client (re)connected, so its card was
    # never drawn / was lost on refresh — replay any still-pending
    # questions for this session as the SAME frame the live path sends,
    # so the frontend's existing card logic redraws with no extra round trip.
    try:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.waits import DurableWaitStore, question_frame_data
        for q in DurableWaitStore(default_store()).list_open(session_id=session_id):
            request = dict(q.request)
            execution = default_store().get_execution(q.execution_id)
            if q.kind == "system_access":
                await ws.send_text(json.dumps({
                    "type": "system_access.waiting",
                    "data": {
                        "id": q.wait_id,
                        "wait_id": q.wait_id,
                        "kind": q.kind,
                        "session_id": session_id,
                        "execution_id": q.execution_id,
                        "required_capabilities": list(request.get("required_capabilities", [])),
                        "capabilities": list(request.get("capabilities", [])),
                        "wait_generation": q.claim_generation,
                        "expected_version": execution.status_version if execution is not None else 0,
                        "expires_at": q.expires_at,
                        "reason_code": "system_access_required",
                        "live": False,
                    },
                }, default=str))
                continue
            await ws.send_text(json.dumps({
                "type": "question.asked",
                "data": question_frame_data(
                    q, session_id=session_id,
                    expected_version=execution.status_version if execution is not None else 0,
                ),
            }, default=str))
    except Exception as e:
        _s._log(f"[load_session] question replay {session_id}: {e}")


async def _after_session_loaded(ws, _s, session_id, run_task, refresh_context_after_load: bool) -> None:
    """What follows the transcript of a first read: the running task, open waits, then context stats."""
    await _send_running_task_state(ws, _s, session_id, run_task)
    await _replay_open_waits(ws, _s, session_id)
    # Cold context accounting is cache warming, not session hydration, and
    # comes last: the controls a running turn or an open question needs
    # never wait for it. refresh_context_stats broadcasts its own
    # context_stats frame when the count is done.
    if refresh_context_after_load:
        await run_for_conversation(session_id, _s.refresh_context_stats, session_id)


async def _read_transcript_page(db, view, cmd: dict):
    """The turns a page request names: ``(turns, window)``."""
    from openprogram.interfaces.server.transcript import reader
    if "history_turn" in cmd:
        return await _session_io(reader.one_turn, db, view, cmd["history_turn"])
    return await _session_io(
        reader.page, db, view,
        before=cmd.get("history_before"), after=cmd.get("history_after"), around=cmd.get("history_around"),
    )


async def _mirror_branch(_s, db, conv: dict, session_id, head, load_started_head) -> None:
    """Keep the in-memory conversation on the head this read showed."""
    with _s._sessions_lock:
        if (_s._sessions.get(session_id) is not conv or conv.get("head_id") != load_started_head
                or (head == load_started_head and conv.get("messages"))):
            return
    try:
        branch = await _session_io(db.get_branch, session_id, head) or []
    except Exception as exc:  # noqa: BLE001  # the mirror is a cache; the read already succeeded
        _log.warning("[load_session] mirror of %s not refreshed: %r", session_id, exc)
        return
    with _s._sessions_lock:
        if _s._sessions.get(session_id) is conv and conv.get("head_id") == load_started_head:
            conv["messages"] = branch
            conv["head_id"] = head


async def _serve_transcript(ws, _s, cmd: dict, conv: dict, session_id, is_history_page: bool, load_started_head, end_parking, _attempt=0) -> None:
    """Answer a protocol 3 read: turns and window straight from the store."""
    from openprogram.interfaces.server.transcript import publisher, reader
    from openprogram.interfaces.server.transcript.turn_index import get_view
    from openprogram.interfaces.server.ws_errors import OperationError
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    selection = await _session_io(_load_selection, db, session_id)
    publisher.hold(ws, session_id)
    try:
        view = await _session_io(get_view, db, session_id, load_started_head)
        publisher.read_view(session_id, view)
        if is_history_page:
            turns, window = await _read_transcript_page(db, view, cmd)
        else:
            turns, window = await _session_io(reader.first_read, db, view)
    except reader.CursorNotOnBranch as exc:
        raise OperationError("cursor_not_on_branch", scope="session", retryable=True) from exc
    except OperationError:
        raise
    except Exception as exc:
        raise _read_failed(session_id, exc) from exc
    if is_history_page:
        await ws.send_text(json.dumps({"type": "session_history_page", "data": {
            "id": session_id, "turns": turns, "window": window,
            "request_id": cmd.get("request_id"), "action": "load_session",
        }}, ensure_ascii=False, default=str))
        end_parking()
        return
    # The publisher can advance the shared view after the page's lock is
    # released. Use the head captured with its rows, not that mutable view.
    head = window["head_id"]
    await _mirror_branch(_s, db, conv, session_id, head, load_started_head)
    try:
        run_task, refresh_context_after_load = await _send_session_loaded(
            ws, _s, cmd, conv, session_id, {"turns": turns, "window": window}, head, view.spawned_branch, selection,
        )
    except _SelectionChanged:
        if _attempt:
            raise OperationError("session_read_failed", scope="session", retryable=True)
        return await _serve_transcript(ws, _s, cmd, conv, session_id, is_history_page,
                                       conv.get("head_id"), end_parking, _attempt=1)
    end_parking()
    await _after_session_loaded(ws, _s, session_id, run_task, refresh_context_after_load)


def _flat_read_snapshot(db, view, byte_limit):
    from copy import deepcopy
    from openprogram.interfaces.server.transcript.flat_rows import flat_rows
    with view.lock:
        rows = flat_rows(db, view, byte_limit=byte_limit)
        return rows, view.head_id, deepcopy(view.spawned_branch)


async def _serve_flat_rows(ws, _s, cmd: dict, conv: dict, session_id, load_started_head, _attempt=0) -> None:
    """Answer a client that is not the web: the branch as plain rows.

    A reply sink with ``flat_row_byte_limit`` (the HTTP command route) gets
    the newest rows that fit; sockets get the whole branch.
    """
    from openprogram.interfaces.server.transcript.turn_index import get_view
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    selection = await _session_io(_load_selection, db, session_id)
    try:
        view = await _session_io(get_view, db, session_id, load_started_head)
        rows, head, spawned_branch = await _session_io(_flat_read_snapshot, db, view, getattr(ws, "flat_row_byte_limit", None))
    except Exception as exc:
        raise _read_failed(session_id, exc) from exc
    await _mirror_branch(_s, db, conv, session_id, head, load_started_head)
    try:
        run_task, refresh_context_after_load = await _send_session_loaded(
            ws, _s, cmd, conv, session_id, {"messages": rows}, head, spawned_branch, selection,
        )
    except _SelectionChanged:
        from openprogram.interfaces.server.ws_errors import OperationError
        if _attempt:
            raise OperationError("session_read_failed", scope="session", retryable=True)
        return await _serve_flat_rows(ws, _s, cmd, conv, session_id, conv.get("head_id"), _attempt=1)
    await _after_session_loaded(ws, _s, session_id, run_task, refresh_context_after_load)


async def _send_session_not_found(ws, cmd: dict, session_id) -> None:
    """Tell the client the session does not exist."""
    # No such session: the client drops the row or tab it asked for
    # instead of showing an empty "New conversation" in its place.
    await ws.send_text(json.dumps({
        "type": "operation_error",
        "data": {
            "action": "load_session",
            "code": "session_not_found",
            "session_id": session_id,
            "request_id": cmd.get("request_id"),
        },
    }, default=str))


async def handle_load_session(ws, cmd: dict):
    """Hydrate a session: linear chain under HEAD + full DAG dump + running-task probe."""
    end_parking = _park_read(ws, cmd.get("session_id"))
    session_id = cmd.get("session_id")
    token = _loading.set(session_id if isinstance(session_id, str) else None)
    try:
        await _handle_load_session(ws, cmd, end_parking)
    finally:
        _loading.reset(token)
        end_parking()


def _park_read(ws, session_id):
    """Hold this session's row frames from before the read's first thread hop."""
    park = getattr(ws, "park_read", None)
    if getattr(ws, "_history_protocol", 0) != 3 or not callable(park) or not isinstance(session_id, str):
        return lambda: None
    return park(session_id)


async def _handle_load_session(ws, cmd: dict, end_parking) -> None:
    from openprogram.interfaces.server import server as _s
    session_id = cmd.get("session_id")
    # A load does not mark the session as viewed: split panes and
    # background loads read sessions nobody is looking at. Only
    # ``mark_session_read`` records the focus that keeps a finishing turn
    # from leaving an unread dot.
    is_history_page = any(key in cmd for key in _HISTORY_PAGE_KEYS)
    if is_history_page:
        _check_page_request(cmd)
    conv = await _find_or_hydrate_session(_s, session_id)
    if conv is None and is_history_page:
        from openprogram.interfaces.server.ws_errors import OperationError
        raise OperationError("invalid_request", scope="session", retryable=True)
    if not conv:
        await _send_session_not_found(ws, cmd, session_id)
        return
    # A chat turn can advance this mirror while the bounded hydration
    # reads run in worker threads. Keep the load payload tied to the
    # captured request, but never let an older load overwrite a newer
    # in-memory head when it returns.
    load_started_head = conv.get("head_id")
    # Hydration is also a recovery boundary.  A worker restart marker can
    # be written during the handoff from a resolved wait to its next
    # continuation; canonical execution state must repair that projection
    # before messages are serialized for the client.
    try:
        from openprogram.runtime.execution.projections.dag import reconcile_session_projection
        if not is_history_page:
            await _session_io(
                reconcile_session_projection, session_id
            )
    except Exception as exc:
        _s._log(f"[load_session] canonical projection repair {session_id}: {exc}")
    if getattr(ws, "_history_protocol", 0) == 3:
        await _serve_transcript(ws, _s, cmd, conv, session_id, is_history_page, load_started_head, end_parking)
        return
    if is_history_page:
        # Pages are a protocol 3 read; other clients get the whole branch.
        from openprogram.interfaces.server.ws_errors import OperationError
        raise OperationError("invalid_request", scope="session")
    await _serve_flat_rows(ws, _s, cmd, conv, session_id, load_started_head)
