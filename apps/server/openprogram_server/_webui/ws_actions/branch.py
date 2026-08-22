"""Branch (git-style) WS actions: list / checkout / rename / auto_name / delete."""
from __future__ import annotations

import asyncio
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
            from openprogram.webui.graph_builder import build_session_graph
            graph = build_session_graph(session_id, active_head)
            leaves = db.list_branches(session_id)
            for row in leaves:
                # An archived branch is retired from the agent list
                # (agent-collaboration.md §2.6). The panel is the same
                # list in visual form, so it drops them too — same
                # filter list_agents applies. They stay in the History
                # graph below, which is the view that shows what exists.
                if row.get("archived"):
                    continue
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
    # 主干 tip（lane 0 最深的对话节点）：HEAD 在这里时顶栏显示 main，
    # 而不是 detached——学 git，主线不是"游离"状态。
    trunk_head = None
    best_depth = -1.0
    for n in graph:
        if (n.get("_lane") or 0) != 0:
            continue
        if n.get("display") in ("root", "runtime"):
            continue
        if n.get("role") not in ("user", "assistant"):
            continue
        d = n.get("_depth") or 0
        if d > best_depth:
            best_depth = d
            trunk_head = n.get("id")
    return {"session_id": session_id, "branches": rows,
            "active": active_head, "trunk_head": trunk_head,
            "graph": graph}


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
    alignment = None
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    elif _s._is_run_active(session_id):
        # Moving HEAD mid-run would leave the in-flight reply anchored
        # to a branch we've already left. Same 409 semantics as
        # retry / edit.
        err = _s.RUN_ACTIVE_ERROR
    else:
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            if not db.message_exists(session_id, head_msg_id):
                err = f"unknown message {head_msg_id!r}"
            else:
                source_head_id = (db.get_session(session_id) or {}).get("head_id")
                from openprogram.agent.workspace_alignment import (
                    conversation_checkout_alignment,
                )

                alignment = conversation_checkout_alignment(
                    session_id, source_head_id, head_msg_id, store=db,
                )
                ok = _s._set_active_head(
                    session_id,
                    head_msg_id,
                    expected_head_id=source_head_id,
                    meta_update={"workspace_alignment": alignment},
                )
                if ok:
                    # A different branch is a different context. The last
                    # measurement belonged to the branch we just left, so
                    # re-estimate against the new one.
                    _s.refresh_context_stats(session_id)
                else:
                    alignment = None
                    err = "conversation head changed during checkout"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_checked_out",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "error": err, "workspace_alignment": alignment},
    }, default=str))


async def handle_resolve_workspace_alignment(ws, cmd: dict):
    from openprogram.webui import server as _s

    session_id = (cmd.get("session_id") or "").strip()
    decision = (cmd.get("decision") or "").strip()
    ok = False
    error = None
    alignment = None
    if not session_id:
        error = "session_id is required"
    elif _s._is_run_active(session_id):
        error = _s.RUN_ACTIVE_ERROR
    elif decision == "keep_current_files":
        from openprogram.agent.session_db import default_db
        from openprogram.agent.workspace_alignment import adopt_current_workspace

        alignment = adopt_current_workspace(session_id, store=default_db())
        ok = alignment.get("status") == "aligned"
        error = alignment.get("resolution_error")
    elif decision == "restore_branch_code":
        from openprogram.agent.session_db import default_db
        from openprogram.agent.workspace_alignment import restore_branch_workspace

        result = await asyncio.to_thread(
            restore_branch_workspace,
            session_id,
            store=default_db(),
            idempotency_key=(cmd.get("idempotency_key") or None),
            source_head_id=(cmd.get("source_head_id") or None),
            target_head_id=(cmd.get("target_head_id") or None),
        )
        ok = result.get("status") == "committed"
        error = result.get("error")
        alignment = result.get("workspace_alignment")
    else:
        error = "unknown workspace alignment decision"
    await ws.send_text(json.dumps({
        "type": "workspace_alignment_resolved",
        "data": {
            "session_id": session_id,
            "decision": decision,
            "ok": ok,
            "error": error,
            "workspace_alignment": alignment,
        },
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
            # User typed a name → highest priority, lock it so auto-naming
            # (Stage 2) never overwrites it (branch-naming.md 优先级与锁).
            default_db().set_branch_name(
                session_id, head_msg_id, new_name, name_locked=True)
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
            from openprogram.agent.dispatcher.titles import (
                build_branch_name_prompt,
            )
            prompt = build_branch_name_prompt(
                db.get_branch(session_id, head_msg_id) or [])
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
                    # User clicked the button → user-initiated = highest
                    # priority. Lock so Stage-2 auto-naming never overwrites
                    # it (branch-naming.md 优先级与锁).
                    db.set_branch_name(
                        session_id, head_msg_id, cleaned, name_locked=True)
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
    elif _s._is_run_active(session_id):
        # Worst case of the unguarded branch actions: the in-flight turn
        # may be writing INTO the tail we're about to delete.
        err = _s.RUN_ACTIVE_ERROR
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
            deleted = db.delete_branch_tail(session_id, head_msg_id)
            # After the tail is gone, so get_branch reads the surviving
            # chain. _set_active_head also refreshes conv["messages"] —
            # required even when HEAD didn't move, since the deleted rows
            # may still sit in the cached list.
            if head_in_branch:
                _s._set_active_head(session_id, new_head)
            else:
                _s._invalidate_messages(session_id)
            # Deleting the branch we were on moves HEAD elsewhere, so the
            # context is a different set of nodes now.
            _s.refresh_context_stats(session_id)
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
                "session_id": session_id or None,
                "ok": False,
                "error": "session_id and target_head_msg_id are required",
            },
        }))
        return

    # Attach appends a row to the ANCHOR session and mark_merged mutates
    # the SOURCE session, so a run in flight on either side can race us.
    _busy = next(
        (s for s in {anchor_session_id, session_id} if _s._is_run_active(s)),
        None,
    )
    if _busy:
        await ws.send_text(json.dumps({
            "type": "attach_branch_result",
            "data": {
                "session_id": session_id,
                "anchor_session_id": anchor_session_id,
                "target_head_msg_id": target_head,
                "ok": False,
                "code": "run_active",
                "error": _s.RUN_ACTIVE_ERROR,
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
                if m.get("predecessor") != anchor:
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
            # predecessor anchors to the conv turn this attach hangs off.
            # No caller, so linear_history skips it and the splicer
            # grafts it back in.
            "predecessor": anchor,
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
        # The attach row landed in the anchor session's DAG, so both the
        # cached branch list and conv["messages"] are now missing it.
        # Re-pin the pre-attach head through _set_active_head to re-read
        # the branch into the mirror and drop the stale cache — without
        # this, _save_session later writes the pre-attach message list
        # back and the card vanishes until a refresh. When there was no
        # pre-attach head, append_message already placed HEAD, so only
        # the cache/mirror needs re-reading.
        if head_before:
            _s._set_active_head(anchor_session_id, head_before)
        else:
            _s._invalidate_messages(anchor_session_id)
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
    else:
        # Defect 6: this frame used to be the ONLY signal and nothing
        # consumed it, so a failed attach was completely silent. Log
        # server-side too so failures are diagnosable from the worker
        # log regardless of what the frontend does with the frame.
        _s._log(
            f"[attach_branch] FAILED session={session_id} "
            f"anchor={anchor_session_id} target={target_head} "
            f"anchor_head={anchor}: {error}"
        )

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
    "resolve_workspace_alignment": handle_resolve_workspace_alignment,
    "rename_branch": handle_rename_branch,
    "auto_name_branch": handle_auto_name_branch,
    "delete_branch_name": handle_delete_branch_name,
    "delete_branch": handle_delete_branch,
    "attach_branch": handle_attach_branch,
}
