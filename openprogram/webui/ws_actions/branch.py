"""Branch (git-style) WS actions: list / checkout / rename / auto_name / delete."""
from __future__ import annotations

import json
from typing import Optional


def _attach_info(m: dict) -> tuple[Optional[str], bool, Optional[str]]:
    """Returns ``(source_head_id, manual, source_commit_id)`` for an
    attach pointer row.

    Source-head is the branch tip the pointer references.
    ``manual=True`` means the user wrote the attach via the Branches
    panel; ``manual=False`` means it was written by a /task spawn.
    ``source_commit_id`` (added with the attach-commit-expansion
    refactor) is the ContextCommit id of the source branch at the
    moment the attach was created — used by generator.py to expand
    the attach into items. Missing on legacy attach rows; callers
    must handle None (fallback to legacy single-item attach).
    """
    if m.get("function") != "attach":
        return None, False, None
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None, False, None
    if isinstance(raw, dict) and "attach" in raw and isinstance(raw["attach"], dict):
        raw = raw["attach"]
    if not isinstance(raw, dict):
        return None, False, None
    h = raw.get("head_id")
    src = h.strip() if isinstance(h, str) and h.strip() else None
    manual = bool(raw.get("manual"))
    cid = raw.get("source_commit_id")
    source_commit_id = (
        cid.strip() if isinstance(cid, str) and cid.strip() else None
    )
    return src, manual, source_commit_id


def _attach_ref(m: dict) -> Optional[str]:
    """Backward-compat shim. Prefer ``_attach_info`` for new code."""
    src, _manual, _src_commit = _attach_info(m)
    return src


def _extract_attach_label(m: dict) -> Optional[str]:
    """``attach.label`` from an attach pointer row, if present."""
    if m.get("function") != "attach":
        return None
    raw = m.get("attach") or m.get("extra")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(raw, dict) and "attach" in raw and isinstance(raw["attach"], dict):
        raw = raw["attach"]
    if isinstance(raw, dict):
        v = raw.get("label")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_function_name(m: dict) -> Optional[str]:
    """For a tool node, the underlying function name (``bash``, ``task``,
    ``read``, ...). Pulled from the legacy ``function`` field with a
    ``extra.tool_use.name`` fallback. ``None`` for non-tool nodes."""
    if m.get("role") != "tool":
        return None
    name = m.get("function")
    if isinstance(name, str) and name:
        return name
    extra = m.get("extra")
    if isinstance(extra, str) and extra:
        try:
            import json as _json
            parsed = _json.loads(extra)
            n = (parsed.get("tool_use") or {}).get("name")
            if isinstance(n, str) and n:
                return n
        except Exception:
            return None
    return None


def _extract_tool_is_error(m: dict) -> bool:
    """Tool node's ``metadata.is_error`` flag (default False)."""
    if m.get("role") != "tool":
        return False
    return bool(m.get("is_error"))


def _extract_llm_meta(m: dict) -> dict:
    """Compact dict of the LLM call stats we surface on the tooltip:
    ``model`` / ``input_tokens`` / ``output_tokens``. Skips fields that
    are absent so the frontend only renders rows that have data."""
    if (m.get("role") or "") not in ("assistant", "llm"):
        return {}
    out: dict = {}
    for k in ("model", "input_tokens", "output_tokens"):
        v = m.get(k)
        if v is not None and v != "":
            out[k] = v
    return out


def _extract_tool_input(m: dict) -> Optional[str]:
    """Pull ``arguments`` out of a tool/code node's extra blob.

    The DAG tooltip uses this to show "what the LLM called this function
    with" alongside the result. Returns a JSON string (pretty-stable
    enough for hover display) or ``None`` for non-tool nodes / when no
    args were captured.
    """
    if m.get("role") != "tool":
        return None
    extra = m.get("extra")
    args = None
    if isinstance(extra, str) and extra:
        try:
            import json as _json
            parsed = _json.loads(extra)
            args = (parsed.get("tool_use") or {}).get("arguments")
        except Exception:
            return None
    elif isinstance(extra, dict):
        args = (extra.get("tool_use") or {}).get("arguments")
    if args is None:
        return None
    if isinstance(args, str):
        return args
    try:
        import json as _json
        return _json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)


def _attach_embed_stats(
    store, session_id: Optional[str], source_commit_id: Optional[str],
) -> tuple[Optional[int], Optional[int]]:
    """Return ``(item_count, total_tokens)`` for the source ContextCommit
    a manual / spawn attach pointer would expand into.

    The frontend uses these to render the embed preview ("EMBEDS N
    messages · M tokens") without a follow-up round trip. Returns
    ``(None, None)`` when the source commit isn't available — frontend
    falls back to the legacy single-message preview.
    """
    if not source_commit_id:
        return None, None
    try:
        from openprogram.context.commit.store import load_commit
        # Same-session lookup is O(1) (direct file read). Don't fall
        # back to a global scan — that walks every session repo on
        # disk and freezes the UI when many sessions exist. The
        # frontend just renders the legacy preview when stats can't
        # be resolved cheaply.
        c = load_commit(store, source_commit_id, session_id=session_id)
    except Exception:
        return None, None
    if c is None:
        return None, None
    # Skip summarized items — they don't render and so wouldn't make
    # it into an expanded attach block either.
    visible = [it for it in c.items if it.state != "summarized"]
    count = len(visible)
    tokens = sum(int(it.tokens or 0) for it in visible)
    return count, tokens


def build_branches_payload(session_id: str | None) -> dict:
    """Build the ``branches_list`` data dict for a session.

    Sync + side-effect-free so any thread can call it — the WS handler
    sends it on request, and the run-path live poller broadcasts it
    while an @agentic_function is executing (so the History graph
    fills in node by node instead of only after the run ends).
    """
    from openprogram.webui import server as _s
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
            # Build a called_by lookup from the underlying Call nodes
            # so graph entries carry the invocation edge (session DAG)
            # alongside the conversation predecessor (parent_id).
            _called_by_map: dict[str, str] = {}
            try:
                _nodes = db.get_nodes(session_id) or []
                for _n in _nodes:
                    if _n.called_by:
                        _called_by_map[_n.id] = _n.called_by
            except Exception:
                pass
            for m in full_msgs:
                content = m.get("content") or ""
                preview = content.strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "…"
                _aref, _amanual, _asrc_commit = _attach_info(m)
                _aembed_n, _aembed_tok = _attach_embed_stats(
                    db, session_id, _asrc_commit,
                )
                _mid = m.get("id") or ""
                graph.append({
                    "id": _mid,
                    "parent_id": m.get("parent_id"),
                    "called_by": _called_by_map.get(_mid, ""),
                    "caller": m.get("caller") or "",
                    "role": m.get("role"),
                    "function": m.get("function"),
                    "display": m.get("display"),
                    "source": m.get("source"),
                    "preview": preview,
                    "input": _extract_tool_input(m),
                    "name": _extract_function_name(m),
                    "is_error": _extract_tool_is_error(m),
                    "llm": _extract_llm_meta(m),
                    "created_at": m.get("created_at"),
                    "attach_ref": _aref,
                    "attach_manual": _amanual,
                    "attach_label": _extract_attach_label(m),
                    "attach_source_commit_id": _asrc_commit,
                    "attach_embed_count": _aembed_n,
                    "attach_embed_tokens": _aembed_tok,
                })
            # Server-side layout — keeps the parallel-branch geometry
            # consistent across load_session, list_branches, and any
            # other path that ships ``graph`` to the frontend.
            from openprogram.webui.graph_layout import annotate_graph
            # ``annotate_graph`` filters out microcompact synthetic
            # nodes (summary_*/k_*) — reassign so the WS payload
            # ships only the DAG-visible subset.
            graph = annotate_graph(graph, active_head)
            leaves = db.list_branches(session_id)
            for row in leaves:
                mid = row["head_msg_id"]
                name = row.get("name")
                if not name:
                    # Unnamed branch → fall back to the head msg id's
                    # short hex prefix (git-style). The user can run
                    # auto_name_branch to get an LLM-summarized label
                    # on demand. Pulling chat content as the label was
                    # confusing (the panel filled up with assistant
                    # reply text) and didn't match git mental model.
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
    return {"session_id": session_id, "branches": rows,
            "active": active_head, "graph": graph}


async def handle_list_branches(ws, cmd: dict):
    payload = build_branches_payload(cmd.get("session_id"))
    await ws.send_text(json.dumps(
        {"type": "branches_list", "data": payload}, default=str))


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


async def handle_attach_branch(ws, cmd: dict) -> None:
    """Write an attach-pointer row anchored on ``anchor_head_msg_id``
    that references the branch ending at ``target_head_msg_id``. Same
    shape as the attach card a /task spawn produces, but explicit and
    decoupled from the active head — the user picks both the source
    branch (what to embed) and the anchor (where the card lives).

    Wire format::

        in:  {"action": "attach_branch", "session_id": "...",
              "target_head_msg_id": "...",       # source (embedded)
              "anchor_head_msg_id": "..."        # where to anchor; default = active head
              "label": "..."                     (optional override)}
        out: broadcast: ``session_reload`` so all tailing clients
                       refresh and see the new attach card.
    """
    import json as _json
    import time
    import uuid

    from openprogram.webui import server as _s

    session_id = (cmd.get("session_id") or "").strip()
    target_head = (cmd.get("target_head_msg_id") or "").strip()
    anchor_arg = (cmd.get("anchor_head_msg_id") or "").strip() or None
    # Cross-session: when ``anchor_session_id`` is supplied, the attach
    # pointer lands on that session instead of ``session_id``. Default
    # = same-session attach (legacy behaviour).
    anchor_session_id = (cmd.get("anchor_session_id") or "").strip() or session_id
    label_override = (cmd.get("label") or "").strip() or None

    if not session_id or not target_head:
        await ws.send_text(json.dumps({
            "type": "attach_branch_result",
            "data": {
                "ok": False,
                "error": "session_id and target_head_msg_id are required",
            },
        }))
        return

    ok = False
    error: str | None = None
    attach_node_id: str | None = None
    anchor: str | None = None
    try:
        from openprogram.agent.session_db import default_db
        db = default_db()
        # Source session (where the embedded content lives) + anchor
        # session (where the attach pointer lands). Same id = legacy
        # same-session attach; different = cross-session attach.
        src_sess = db.get_session(session_id) or {}
        anchor_sess = (
            db.get_session(anchor_session_id) if anchor_session_id != session_id
            else src_sess
        ) or {}
        # Anchor: caller-supplied head_msg_id or fall back to the
        # anchor session's active head. Caller specifies it so the
        # user can "attach branch X onto branch Y" without first
        # having to switch to Y.
        anchor = anchor_arg or anchor_sess.get("head_id")
        if not anchor:
            raise RuntimeError(
                f"anchor session {anchor_session_id!r} has no active head"
            )
        if anchor_session_id == session_id and anchor == target_head:
            raise RuntimeError(
                "cannot attach a branch to itself "
                "(anchor and target are the same head)"
            )
        # Dedupe: if the anchor session already has an attach pointer
        # hanging off this anchor that references the same source head,
        # don't write another one — it would draw a duplicate edge in
        # the DAG and double up the attach card in chat.
        try:
            anchor_msgs = db.get_messages(anchor_session_id) or []
            for m in anchor_msgs:
                if m.get("function") != "attach":
                    continue
                if m.get("called_by") != anchor:
                    continue
                ad = m.get("attach")
                if isinstance(ad, dict) and (ad.get("head_id") or "").strip() == target_head:
                    await ws.send_text(json.dumps({
                        "type": "attach_branch_result",
                        "data": {
                            "session_id": session_id,
                            "anchor_session_id": anchor_session_id,
                            "target_head_msg_id": target_head,
                            "anchor": anchor,
                            "attach_node_id": m.get("id"),
                            "ok": True,
                            "duplicate": True,
                            "error": None,
                        },
                    }, default=str))
                    return
        except Exception:
            pass
        # Resolve the target branch's name + a short content preview
        # from the SOURCE session so the AttachCard can render label +
        # preview without a follow-up round trip.
        target_label = label_override or ""
        target_preview = ""
        try:
            branches = db.list_branches(session_id) or []
            for b in branches:
                if b.get("head_msg_id") == target_head:
                    target_label = target_label or (b.get("name") or "")
                    break
            chain = db.get_branch(session_id, target_head) or []
            for r in reversed(chain):
                if (
                    r.get("role") == "assistant"
                    and isinstance(r.get("content"), str)
                    and r.get("function") != "attach"
                ):
                    target_preview = r["content"]
                    break
        except Exception:
            pass

        # Look up the source branch's current ContextCommit id so the
        # generator can expand the attach pointer into a copy of that
        # commit's items on the next turn (see
        # docs/design/context/context-attach-merge.md, scenario B). Missing /
        # absent → legacy single-item fallback path in the generator.
        source_commit_id = None
        try:
            from openprogram.context.commit.store import load_commit_for_head
            src_commit = load_commit_for_head(db, session_id, target_head)
            if src_commit is not None:
                source_commit_id = src_commit.id
        except Exception:
            pass

        attach_node_id = uuid.uuid4().hex[:12]
        attach_msg = {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": (target_preview or "(no preview)").strip(),
            # Same convention as the /task-produced attach pointer:
            # called_by anchors to the conv turn this attach hangs off.
            # No parent_id, so linear_history skips it and the splicer
            # grafts it back in.
            "called_by": anchor,
            "timestamp": time.time(),
            "agent_id": (anchor_sess.get("agent_id") or "main"),
            "extra": _json.dumps({
                "attach": {
                    # Source session/head the card embeds.
                    "session_id": session_id,
                    "head_id": target_head,
                    "label": target_label,
                    "manual": True,
                    # Pinned ContextCommit id at the source branch tip
                    # the moment this attach pointer was written. Used
                    # by generator.py to expand the source commit's
                    # items into the next turn's commit; never updated
                    # afterwards (the attach is frozen to this
                    # moment). None when the source branch had no
                    # commit yet (legacy fallback path).
                    "source_commit_id": source_commit_id,
                },
            }, default=str),
        }
        # Write the pointer onto the ANCHOR session (where the card is
        # supposed to appear), not necessarily the source session.
        head_before = anchor_sess.get("head_id")
        db.append_message(anchor_session_id, attach_msg)
        if head_before:
            try:
                db.set_head(anchor_session_id, head_before)
            except Exception:
                pass
        try:
            db.commit_turn(
                anchor_session_id,
                f"attach branch: {target_label or target_head[:8]}",
            )
        except Exception:
            pass
        # Manual attach consumes the source branch — its content is
        # now embedded into the anchor lane, so the sub-branch should
        # disappear from the Branches panel (same semantics as merge
        # turn). Apply only when source == anchor (same-session
        # attach); cross-session attaches don't own the source head.
        if anchor_session_id == session_id:
            try:
                db.mark_merged(session_id, [target_head])
            except Exception:
                pass
        ok = True
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    if ok:
        try:
            # Reload the anchor session (where the card lands). If
            # source != anchor, the source session is unchanged.
            _s._broadcast(json.dumps({
                "type": "session_reload",
                "data": {
                    "session_id": anchor_session_id,
                    "reason": "attach",
                },
            }, default=str))
        except Exception:
            pass

    await ws.send_text(json.dumps({
        "type": "attach_branch_result",
        "data": {
            "session_id": session_id,
            "anchor_session_id": anchor_session_id,
            "target_head_msg_id": target_head,
            "anchor": anchor,
            "attach_node_id": attach_node_id,
            "ok": ok,
            "error": error,
        },
    }, default=str))


ACTIONS = {
    "list_branches": handle_list_branches,
    "checkout_branch": handle_checkout_branch,
    "rename_branch": handle_rename_branch,
    "auto_name_branch": handle_auto_name_branch,
    "delete_branch_name": handle_delete_branch_name,
    "delete_branch": handle_delete_branch,
    "attach_branch": handle_attach_branch,
}
