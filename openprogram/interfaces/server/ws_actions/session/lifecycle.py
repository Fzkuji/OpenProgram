"""Session lifecycle actions: delete, rename, flags, clear, mark read, next-prompt prediction."""
from __future__ import annotations

import logging
import asyncio
import json

from openprogram.state.store.session.conversation_work import run_for_conversation

from .listing import handle_list_sessions

_log = logging.getLogger(__name__)


def _close_browser_tabs(session_id: str) -> None:
    from openprogram.programs.applications.browser.tabs import close_conversation_tabs_soon
    close_conversation_tabs_soon(session_id)


async def handle_delete_session(ws, cmd: dict):
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.context.session_db import default_db

    session_id = cmd.get("session_id")
    if not session_id:
        return
    # A running turn, its approval prompts and its queued messages go with
    # the session; otherwise the turn keeps writing into a deleted repo and
    # its questions stay open in other tabs. Cancelling waits on the
    # execution owner, so it runs off the event loop.
    await run_for_conversation(session_id, 
        _s._stop_session_work, session_id, reason="the target session was deleted",
    )
    with _s._sessions_lock:
        conv = _s._sessions.pop(session_id, None)
    if conv:
        if conv.get("runtime") and hasattr(conv["runtime"], "close"):
            conv["runtime"].close()
        _s._cleanup_session_resources(session_id, conv)
    try:
        await run_for_conversation(session_id, default_db().delete_session, session_id)
    except Exception as e:
        _s._log(f"[delete_session] {session_id}: {e}")
        # The sender already dropped the row; hand it the real list so a
        # session that is still on disk comes back instead of reappearing
        # only after a reload.
        await handle_list_sessions(ws, {})
        return
    _close_browser_tabs(session_id)
    _s._broadcast(json.dumps({"type": "session_deleted", "session_id": session_id}))


_rename_tasks: set[asyncio.Task] = set()


async def handle_rename_session(ws, cmd: dict):
    """Set a conversation's display title.

    When ``title`` is provided: write it directly (manual rename) and
    lock it: background titling never replaces a typed title.
    When ``title`` is absent/empty ("Generate a name"): ask the title
    model now, from the same branch context background titling uses
    (``build_title_context``). A generated name does not lock: it clears
    ``_user_titled``, so later background retitles can still improve it,
    while a typed title always wins.

    Persists to ``meta.json`` via ``update_session`` AND patches the
    in-memory ``_sessions`` dict (if the conv is hydrated) so the
    next ``list_sessions`` and any live reference both see the new
    title. Echoes ``session_updated`` so every connected client can
    patch the one row without a full re-list.
    """
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.context.session_db import default_db

    session_id = cmd.get("session_id")

    async def reply(status: str, title: str | None = None):
        try:
            await ws.send_text(json.dumps({
                "type": "session_rename_result",
                "data": {"session_id": session_id, "action": "rename_session",
                         "request_id": cmd.get("request_id"), "status": status,
                         "title": title},
            }))
        except Exception:
            # Persistence and broadcasts remain valid if the requesting socket left.
            _s._log(f"[rename_session] {session_id}: reply not delivered")

    db = default_db()
    # Every store call here takes the conversation's lock: off the loop.
    before = (await run_for_conversation(session_id, db.get_session, session_id)
              if isinstance(session_id, str) else None)
    if not before:
        await reply("failed")
        return
    title = cmd.get("title")
    if title is not None and not isinstance(title, str):
        await reply("failed")
        return
    title = (title or "").strip()
    async def finish():
        nonlocal title
        generated = not title
        if generated:
            try:
                title = await asyncio.wait_for(asyncio.to_thread(_llm_rename, session_id), 60)
            except Exception as exc:
                _s._log(f"[rename_session] {session_id}: {type(exc).__name__}")
                await reply("failed")
                return
            current = await run_for_conversation(session_id, db.get_session, session_id)
            if (not current or current.get("title") != before.get("title")
                    or (current.get("extra_meta") or {}).get("_user_titled")
                    != (before.get("extra_meta") or {}).get("_user_titled")):
                await reply("superseded")
                return
            if not isinstance(title, str) or not title.strip():
                await reply("failed")
                return
            title = title.strip()
        if generated:
            # An automatic title made on request: it marks the session
            # titled (no phase-1 re-truncation) but leaves later retitles
            # free to improve it.
            gen_count = int((current.get("extra_meta") or {}).get("_title_gen_count") or 0)
            fields = {"_user_titled": False, "_auto_titled": True,
                      "_title_gen_count": max(1, gen_count)}
        else:
            # A typed title is the user's choice; background titling
            # never replaces it.
            fields = {"_user_titled": True}
        try:
            await run_for_conversation(session_id, lambda: db.update_session(session_id, title=title, **fields))
        except Exception as exc:
            _s._log(f"[rename_session] {session_id}: {type(exc).__name__}")
            await reply("failed")
            return
        if session_id in _s._sessions:
            _s._sessions[session_id]["title"] = title
        _s._broadcast(json.dumps({
            "type": "session_updated",
            "data": {"id": session_id, "title": title},
        }, default=str))
        await reply("ok", title)

    if title:
        await finish()
        return None

    async def run():
        try:
            await finish()
        except Exception as exc:
            _s._log(f"[rename_session] {session_id}: {type(exc).__name__}")
            await reply("failed")

    # The socket dispatcher awaits handlers serially. Return immediately so
    # navigation, chat, and a newer manual rename can use this same connection.
    task = asyncio.create_task(run())
    _rename_tasks.add(task)
    task.add_done_callback(_rename_tasks.discard)
    return task


async def handle_predict_next_prompt(ws, cmd: dict):
    """Suggest the user's next message once a turn has ended (Codex composer predictions).

    Answers ``next_prompt_prediction`` with ``text`` (None when there is no
    suggestion). Runs in the background so the socket stays free.
    """
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.conversation.turn_runtime.next_prompt import predict_next_prompt

    session_id = cmd.get("session_id")

    async def run():
        text = None
        if isinstance(session_id, str) and session_id:
            try:
                text = await asyncio.wait_for(asyncio.to_thread(predict_next_prompt, session_id), 30)
            except Exception as exc:
                _s._log(f"[predict_next_prompt] {session_id}: {type(exc).__name__}")
        try:
            await ws.send_text(json.dumps({
                "type": "next_prompt_prediction",
                "data": {"session_id": session_id, "request_id": cmd.get("request_id"), "text": text},
            }))
        except Exception:
            # The requesting socket closed; the prediction is only a
            # suggestion.
            _log.debug(
                "next prompt prediction not delivered for session %s", session_id, exc_info=True)

    task = asyncio.create_task(run())
    _rename_tasks.add(task)
    task.add_done_callback(_rename_tasks.discard)
    return task


def _llm_rename(session_id: str) -> str | None:
    """Generate a title via LLM for the current branch, from the same
    context background titling uses (``build_title_context``)."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.turn_runtime import titles

    db = default_db()
    history = db.get_branch(session_id) or []
    if titles.build_title_context(history):
        return titles.generate_title_for_messages(history)
    # Function-only sessions have execution records rather than user turns.
    user_text = (db.get_session(session_id) or {}).get("title") or ""
    assistant_text = next((
        msg.get("content", "") for msg in reversed(history)
        if msg.get("role") == "code" and isinstance(msg.get("content"), str)
    ), "")
    if not user_text:
        return None
    return titles._generate_llm_title(str(user_text), str(assistant_text))


async def handle_update_session_flags(ws, cmd: dict):
    """Set conversation management flags: pinned / archived / group /
    sort_at.

    ``sort_at`` is a place in the project list: the time the chat sorts
    by instead of its last activity, held until it has activity of its
    own again (``sort_since`` records the activity it was placed at;
    ``sort_at`` 0 clears both). Only the keys present in ``cmd`` are
    written, so a pin toggle doesn't clobber the group and vice-versa. ``group`` may be an
    empty string to ungroup (``update_session`` drops ``None`` but
    keeps ``""`` / ``False``, so booleans + the ungroup sentinel
    persist correctly). Dual-writes meta.json + the in-memory conv
    and echoes ``session_updated``.
    """
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.context.session_db import default_db

    session_id = cmd.get("session_id")
    if not session_id:
        return
    fields: dict = {}
    if "pinned" in cmd:
        fields["pinned"] = bool(cmd.get("pinned"))
    if "archived" in cmd:
        fields["archived"] = bool(cmd.get("archived"))
    if "group" in cmd:
        g = cmd.get("group")
        fields["group"] = "" if g is None else str(g)
    sort_at = None
    if "sort_at" in cmd:
        try:
            sort_at = float(cmd.get("sort_at") or 0)
        except (TypeError, ValueError):
            sort_at = 0.0
        fields["sort_at"] = sort_at
    if not fields:
        return

    def write() -> bool:
        """Write the flags; whether the conversation was archived before.
        Read and written under the conversation's lock: in its lane."""
        row = default_db().get_session(session_id) or {}
        # Read before the write: the row may be the live conversation's dict.
        was_archived = bool(row.get("archived"))
        if sort_at is not None:
            fields["sort_since"] = float(row.get("updated_at") or 0) if sort_at else 0.0
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            if conv is not None:
                conv.update(fields)
        default_db().update_session(session_id, **fields)
        return was_archived

    try:
        was_archived = await run_for_conversation(session_id, write)
    except Exception as e:
        _s._log(f"[update_session_flags] {session_id}: {e}")
    else:
        if fields.get("archived"):
            _close_browser_tabs(session_id)
        if "archived" in fields and fields["archived"] != was_archived:
            from openprogram.runtime.conversation.always_on.runtime import archive_changed
            await run_for_conversation(session_id, archive_changed, session_id, fields["archived"])
    _s._broadcast(json.dumps({
        "type": "session_updated",
        "data": {"id": session_id, **fields},
    }, default=str))


async def handle_clear_sessions(ws, cmd: dict):
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.context.session_db import default_db

    db = default_db()

    # 真相来源是 SessionStore（git repos）。"清空全部" = 把 SessionStore 里
    # 枚举到的每个 session 都 delete_session(sid) 掉，外加任何还在内存但已
    # 不在磁盘的。一律按 session_id 直接 destroy git repo，不碰 agent_id ——
    # 旧逻辑「按 agent_id 删 / 查不到就扫文件系统」会漏掉没有 agent_id 的
    # 空壳会话（forced-tool-call 建的），删了刷新又出现。这里收口到真相来源，
    # 一次删干净。
    ids: set[str] = set()
    try:
        for row in db.list_sessions(limit=10**9, include_archived=True):
            sid = row.get("id")
            if sid:
                ids.add(sid)
    except Exception as e:
        _s._log(f"[clear_sessions] list: {e}")

    with _s._sessions_lock:
        ids.update(_s._sessions)
    # 先停掉每个会话里还在跑的 turn、待答的审批和排队消息，再删仓库；
    # 否则 turn 会继续写已删除的会话。取消要等执行方确认，放到线程里。
    def _stop_all() -> None:
        for cid in ids:
            _s._stop_session_work(cid, reason="the target session was deleted")

    await asyncio.to_thread(_stop_all)

    # 内存里活着的会话：先关 runtime，再纳入待删集合（覆盖磁盘没枚举到的）。
    with _s._sessions_lock:
        for sid, conv in _s._sessions.items():
            ids.add(sid)
            if conv.get("runtime") and hasattr(conv["runtime"], "close"):
                try:
                    conv["runtime"].close()
                except Exception:
                    # Sessions are deleted below regardless; a failed close
                    # only leaks the runtime.
                    _log.debug(
                        "closing runtime of session %s failed", sid, exc_info=True)
        _s._sessions.clear()

    for cid in ids:
        _s._follow_up_queues.pop(cid, None)
        with _s._running_tasks_lock:
            _s._running_tasks.pop(cid, None)
        try:
            # Deleted under the conversation's lock: in its lane.
            await run_for_conversation(cid, db.delete_session, cid)
            _close_browser_tabs(cid)
        except Exception as e:
            _s._log(f"[clear_sessions] delete {cid}: {e}")

    # 删完回发一次真实列表（应为空），让发起方立刻看到清空结果，不靠刷新。
    await handle_list_sessions(ws, {})


async def handle_set_window_hidden(ws, cmd: dict):
    """Record whether this client's window is hidden (minimized or in the background)."""
    ws._window_hidden = bool(cmd.get("hidden"))


async def handle_mark_session_read(ws, cmd: dict):
    """Mark a conversation as seen + record that this client is viewing it.

    Opening a conversation in the UI is otherwise pure client-side routing,
    so this is the only signal the server gets that a session was looked at.
    Clears the ``unread`` flag (blue dot) and remembers
    ``ws._focused_session_id`` so a later background-run completion knows not
    to re-mark this session unread for a client that's actively on it.
    Broadcasts so every open tab clears the dot together. A hidden window
    keeps its focus but has not seen anything, so dot and news stay.
    """
    from openprogram.interfaces.server import server as _s
    from openprogram.runtime.context.session_db import default_db

    sid = cmd.get("session_id") or None
    ws._focused_session_id = sid
    if not sid or getattr(ws, "_window_hidden", False):
        return
    with _s._sessions_lock:
        conv = _s._sessions.get(sid)
        if conv is not None:
            conv["unread"] = False
    try:
        # Writing the flag takes the conversation's lock: off the loop.
        await run_for_conversation(sid, default_db().update_session, sid, unread=False)
    except Exception as e:
        _s._log(f"[mark_session_read] {sid}: {e}")
    _s._broadcast(json.dumps({
        "type": "session_updated",
        "data": {"id": sid, "unread": False},
    }, default=str))
    try:
        from openprogram.runtime.conversation.always_on import pending
        if await run_for_conversation(sid, pending.mark_seen, sid):
            # Its news left the pending inbox.
            _s._broadcast(json.dumps({"type": "always_on_update", "data": {"session_id": sid}}))
    except Exception as e:  # noqa: BLE001 - reading the conversation goes on
        _s._log(f"[mark_session_read] pending {sid}: {e}")
