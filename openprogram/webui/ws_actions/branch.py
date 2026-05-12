"""Branch (git-style) WS actions: list / checkout / rename / auto_name / delete."""
from __future__ import annotations

import json


async def handle_list_branches(ws, cmd: dict):
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    rows: list[dict] = []
    active_head = None
    if session_id:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            sess = db.get_session(session_id)
            active_head = (sess or {}).get("head_id")
            leaves = db.list_branches(session_id)
            for row in leaves:
                mid = row["head_msg_id"]
                name = row.get("name")
                if not name:
                    cur = db.conn.execute(
                        "WITH RECURSIVE chain(id, parent_id, role, content, ts) AS ("
                        "  SELECT id, parent_id, role, content, timestamp "
                        "    FROM messages WHERE id=? AND session_id=?"
                        "  UNION ALL"
                        "  SELECT m.id, m.parent_id, m.role, m.content, m.timestamp"
                        "    FROM messages m JOIN chain c ON m.id = c.parent_id"
                        "    WHERE m.session_id=?"
                        ") SELECT content FROM chain "
                        "WHERE role IN ('user','assistant') "
                        "ORDER BY ts DESC LIMIT 1",
                        (mid, session_id, session_id),
                    )
                    r = cur.fetchone()
                    if r and isinstance(r[0], str):
                        txt = r[0].strip().replace("\n", " ")
                        name = (txt[:40] + "…") if len(txt) > 40 else txt
                    else:
                        name = mid[:8]
                rows.append({
                    "head_msg_id": mid,
                    "name": name,
                    "is_named": bool(row.get("name")),
                    "created_at": row.get("created_at"),
                    "active": (mid == active_head),
                })
        except Exception as e:
            _s._log(f"[list_branches] {session_id}: {e}")
    await ws.send_text(json.dumps({
        "type": "branches_list",
        "data": {"session_id": session_id, "branches": rows, "active": active_head},
    }, default=str))


async def handle_checkout_branch(ws, cmd: dict):
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    ok = False
    err = None
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    else:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            cur = db.conn.execute(
                "SELECT 1 FROM messages WHERE id=? AND session_id=?",
                (head_msg_id, session_id),
            )
            if not cur.fetchone():
                err = f"unknown message {head_msg_id!r}"
            else:
                db.set_head(session_id, head_msg_id)
                with _s._sessions_lock:
                    c = _s._sessions.get(session_id)
                    if c is not None:
                        c["head_id"] = head_msg_id
                        c["messages"] = db.get_branch(session_id) or []
                _s._invalidate_messages(session_id)
                ok = True
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_checked_out",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "error": err},
    }, default=str))


async def handle_rename_branch(ws, cmd: dict):
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    new_name = (cmd.get("name") or "").strip()
    ok = False
    err = None
    if not head_msg_id and session_id:
        try:
            from openprogram.agent.session_db import default_db
            _sess = default_db().get_session(session_id) or {}
            head_msg_id = _sess.get("head_id")
        except Exception:
            pass
    if not session_id or not head_msg_id or not new_name:
        err = "session_id, head_msg_id, name all required"
    elif len(new_name) > 80:
        err = "name too long (max 80)"
    else:
        try:
            from openprogram.agent.session_db import default_db
            default_db().set_branch_name(session_id, head_msg_id, new_name)
            ok = True
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_renamed",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "name": new_name, "ok": ok, "error": err},
    }, default=str))


async def handle_auto_name_branch(ws, cmd: dict):
    """AI-generated short branch label from the branch's tail context."""
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    if not head_msg_id and session_id:
        try:
            from openprogram.agent.session_db import default_db
            _sess = default_db().get_session(session_id) or {}
            head_msg_id = _sess.get("head_id")
        except Exception:
            pass
    ok = False
    err = None
    name = None
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    else:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            chain = []
            cur = head_msg_id
            while cur:
                row = db.conn.execute(
                    "SELECT id, parent_id, role, content "
                    "FROM messages WHERE session_id=? AND id=?",
                    (session_id, cur),
                ).fetchone()
                if row is None:
                    break
                chain.insert(0, row)
                cur = row[1]
            recent = chain[-6:]
            transcript = "\n\n".join(
                f"[{r[2] or '?'}] {(r[3] or '').strip()}"
                for r in recent if r[3]
            )[:2000]
            prompt = (
                "Summarize the topic of this conversation as a "
                "very short branch label. Reply with ONLY the label "
                "itself — 2 to 6 words, no quotes, no trailing "
                "punctuation, in the same language as the conversation.\n\n"
                + transcript
            )
            from openprogram.webui import _runtime_management as rm
            rm._init_providers()
            rt = rm._chat_runtime
            if rt is None:
                err = "no LLM runtime available"
            else:
                import asyncio as _a
                reply = await _a.to_thread(
                    rt.exec, content=[{"type": "text", "text": prompt}]
                )
                cleaned = (str(reply or "")
                           .strip()
                           .strip('"\'')
                           .splitlines()[0]
                           if reply else "")
                cleaned = cleaned.strip().strip('"\'').rstrip(".。")
                if cleaned:
                    if len(cleaned) > 40:
                        cleaned = cleaned[:40].rstrip() + "…"
                    db.set_branch_name(session_id, head_msg_id, cleaned)
                    name = cleaned
                    ok = True
                else:
                    err = "LLM returned empty response"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_renamed",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "name": name, "ok": ok, "error": err, "auto": True},
    }, default=str))


async def handle_delete_branch_name(ws, cmd: dict):
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    ok = False
    err = None
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    else:
        try:
            from openprogram.agent.session_db import default_db
            default_db().delete_branch_name(session_id, head_msg_id)
            ok = True
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_name_deleted",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "error": err},
    }, default=str))


async def handle_delete_branch(ws, cmd: dict):
    """Real branch delete — walks the unique tail up to the fork point."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    if not head_msg_id and session_id:
        try:
            from openprogram.agent.session_db import default_db as _df
            _sess = _df().get_session(session_id) or {}
            head_msg_id = _sess.get("head_id")
        except Exception:
            pass
    ok = False
    err = None
    deleted = 0
    new_head = None
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    else:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            sess = db.get_session(session_id) or {}
            cur_head = sess.get("head_id")
            head_in_branch = False
            if cur_head:
                walk = cur_head
                while walk:
                    if walk == head_msg_id:
                        head_in_branch = True
                        break
                    row = db.conn.execute(
                        "SELECT parent_id FROM messages WHERE session_id=? AND id=?",
                        (session_id, walk),
                    ).fetchone()
                    if row is None:
                        break
                    walk = row[0]
            if head_in_branch:
                leaves = db.list_branches(session_id)
                for lf in leaves:
                    if lf["head_msg_id"] != head_msg_id:
                        new_head = lf["head_msg_id"]
                        break
                if new_head:
                    db.set_head(session_id, new_head)
            deleted = db.delete_branch_tail(session_id, head_msg_id)
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
                if conv is not None:
                    if new_head:
                        conv["head_id"] = new_head
                        try:
                            conv["messages"] = db.get_branch(session_id) or []
                        except Exception:
                            pass
            _s._invalidate_messages(session_id)
            ok = True
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_deleted",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "deleted": deleted,
                  "new_head": new_head, "error": err},
    }, default=str))


ACTIONS = {
    "list_branches": handle_list_branches,
    "checkout_branch": handle_checkout_branch,
    "rename_branch": handle_rename_branch,
    "auto_name_branch": handle_auto_name_branch,
    "delete_branch_name": handle_delete_branch_name,
    "delete_branch": handle_delete_branch,
}
