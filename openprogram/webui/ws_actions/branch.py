"""Branch (git-style) WS actions: list / checkout / rename / auto_name / delete."""
from __future__ import annotations

import json


async def handle_list_branches(ws, cmd: dict):
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    rows: list[dict] = []
    active_head = None
    graph: list[dict] = []
    if session_id:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            sess = db.get_session(session_id)
            active_head = (sess or {}).get("head_id")
            try:
                full_msgs = db.get_messages(session_id) or []
            except Exception:
                full_msgs = []
            for m in full_msgs:
                content = m.get("content") or ""
                preview = content.strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "…"
                graph.append({
                    "id": m.get("id"),
                    "parent_id": m.get("parent_id"),
                    "role": m.get("role"),
                    "function": m.get("function"),
                    "display": m.get("display"),
                    "preview": preview,
                    "created_at": m.get("created_at"),
                })
            leaves = db.list_branches(session_id)
            for row in leaves:
                mid = row["head_msg_id"]
                name = row.get("name")
                if not name:
                    # Walk the chain back from the tip; pick the most
                    # recent user/assistant message's content as the
                    # auto-label.
                    chain = db.get_branch(session_id, mid) or []
                    latest_text = None
                    for r in reversed(chain):
                        if r.get("role") in ("user", "assistant") \
                                and isinstance(r.get("content"), str):
                            latest_text = r["content"]
                            break
                    if latest_text:
                        txt = latest_text.strip().replace("\n", " ")
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
        "data": {"session_id": session_id, "branches": rows, "active": active_head, "graph": graph},
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
            if not db.message_exists(session_id, head_msg_id):
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
            chain = db.get_branch(session_id, head_msg_id) or []
            recent = chain[-6:]
            transcript = "\n\n".join(
                f"[{m.get('role') or '?'}] {(m.get('content') or '').strip()}"
                for m in recent if m.get("content")
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
                chain = db.get_branch(session_id, cur_head) or []
                head_in_branch = any(m.get("id") == head_msg_id for m in chain)
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
