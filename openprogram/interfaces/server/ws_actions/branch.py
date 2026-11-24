"""Branch (git-style) WS actions: list / checkout / rename / auto_name / delete,
attach, and branching a conversation into a new session."""
from __future__ import annotations

import logging
import json
import uuid

from openprogram.state.store.session.conversation_work import run_for_conversation
from openprogram.state.store.session.graph.branches import (  # noqa: F401
    _attach_info,
    _attach_ref,
    _extract_attach_session_id,
    _is_merge_temp_attach,
    _extract_attach_label,
    _extract_attach_job_id,
    _extract_function_name,
    _extract_tool_is_error,
    _extract_llm_meta,
    _extract_tool_input,
    _attach_embed_stats,
    _spawned_heads,
    build_branches_payload,
)

_log = logging.getLogger(__name__)


async def handle_list_branches(ws, cmd: dict):
    if "graph_visible" in cmd:
        ws._history_graph_session = cmd.get("session_id") if cmd["graph_visible"] else None
        if not cmd["graph_visible"]:
            return
    payload = await run_for_conversation(cmd.get("session_id"), build_branches_payload, cmd.get("session_id"))
    if cmd.get("include_graph") is False:
        payload.pop("graph", None)
    await ws.send_text(json.dumps(
        {"type": "branches_list", "data": payload}, default=str))


async def handle_checkout_branch(ws, cmd: dict):
    """Select the branch ending at ``head_msg_id``: a view change, never
    refused because a turn runs on the branch left or the branch entered
    (``switch_branch``)."""
    from openprogram.interfaces.server import server as _s
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    if not session_id or not head_msg_id:
        ok, err, alignment, active_branch_id = False, "session_id and head_msg_id required", None, None
    else:
        # Every read and write here takes the conversation's lock: off the
        # event loop, so a held conversation delays only this request.
        ok, err, alignment, active_branch_id = await run_for_conversation(session_id, 
            _checkout_branch, _s, cmd, session_id, head_msg_id)
    await ws.send_text(json.dumps({
        "type": "branch_checked_out",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "error": err, "workspace_alignment": alignment,
                  **({"active_branch_id": active_branch_id} if active_branch_id else {})},
    }, default=str))


def _checkout_branch(_s, cmd: dict, session_id: str, head_msg_id: str):
    """``(ok, error, workspace_alignment, active_branch_id)`` of a switch."""
    ok, err, alignment, active_branch_id = False, None, None, None
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        if (head_msg_id in set((db.get_session(session_id) or {}).get("rewind_hidden_nodes") or [])
                or head_msg_id in set((db.get_session(session_id) or {}).get("rewind_hidden_heads") or [])):
            err = "This version was withdrawn by Rewind; use Undo rewind to recover it."
        elif not db.message_exists(session_id, head_msg_id):
            err = f"unknown message {head_msg_id!r}"
        else:
            alignment = {"status": "aligned", "decision": "shared_workspace"}
            from openprogram.runtime.conversation.rewinds.rewind_marks import forget_read_baselines
            # Every branch works on the same current files, not a historical copy.
            switched = _s.switch_branch(
                session_id, head_msg_id,
                branch_id=cmd.get("branch_id"),
                meta_update={"workspace_alignment": alignment,
                             "pending_rewind_note": {"kind": "branch_kept_files"}},
            )
            ok = switched["ok"]
            if ok:
                active_branch_id = switched["active_branch_id"]
                forget_read_baselines(session_id)
                # A different branch is a different context. The last
                # measurement belonged to the branch we just left, so
                # re-estimate against the new one.
                _s.refresh_context_stats(session_id)
            else:
                alignment = None
                err = switched["error"]
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    return ok, err, alignment, active_branch_id


async def handle_resolve_workspace_alignment(ws, cmd: dict):
    from openprogram.interfaces.server import server as _s

    session_id = (cmd.get("session_id") or "").strip()
    decision = (cmd.get("decision") or "").strip()
    ok = False
    error = None
    alignment = None
    if not session_id:
        error = "session_id is required"
    elif await run_for_conversation(session_id, _s._is_run_active, session_id):
        error = _s.RUN_ACTIVE_ERROR
    elif decision == "keep_current_files":
        # Compatibility for an already-open client displaying the retired banner.
        alignment = {"status": "aligned", "decision": "shared_workspace"}
        from openprogram.runtime.context.session_db import default_db
        await run_for_conversation(session_id, lambda: default_db().update_session(
            session_id, workspace_alignment=alignment))
        ok = True
    elif decision == "restore_branch_code":
        error = "Use Rewind to preview and confirm a file restore. Switching versions keeps current files."
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


def _stored_head(session_id: str):
    """The conversation's head, read under its lock (callers run this off
    the event loop); ``None`` when it has none."""
    try:
        from openprogram.runtime.context.session_db import default_db
        return (default_db().get_session(session_id) or {}).get("head_id")
    except Exception:
        # No stored head: the caller's missing-head check answers the client.
        _log.debug("head lookup failed for session %s", session_id, exc_info=True)
        return None


async def handle_rename_branch(ws, cmd: dict):
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    new_name = (cmd.get("name") or "").strip()
    ok = False
    err = None
    if not head_msg_id and session_id:
        head_msg_id = await run_for_conversation(session_id, _stored_head, session_id)
    if not session_id or not head_msg_id or not new_name:
        err = "session_id, head_msg_id, name all required"
    elif len(new_name) > 80:
        err = "name too long (max 80)"
    else:
        try:
            from openprogram.runtime.context.session_db import default_db
            # User typed a name → highest priority, lock it so auto-naming
            # (Stage 2) never overwrites it (branch-naming.md 优先级与锁).
            await run_for_conversation(session_id, lambda: default_db().set_branch_name(
                session_id, head_msg_id, new_name, name_locked=True))
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
        head_msg_id = await run_for_conversation(session_id, _stored_head, session_id)
    ok = False
    err = None
    name = None
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    else:
        try:
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            from openprogram.runtime.conversation.turn_runtime.titles import (
                build_branch_name_prompt,
            )
            prompt = build_branch_name_prompt(
                await run_for_conversation(session_id, db.get_branch, session_id, head_msg_id) or [])
            from openprogram.interfaces.server import runtime_management as rm
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
                    await run_for_conversation(session_id, lambda: db.set_branch_name(
                        session_id, head_msg_id, cleaned, name_locked=True))
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
            from openprogram.runtime.context.session_db import default_db
            await run_for_conversation(session_id, default_db().delete_branch_name, session_id, head_msg_id)
            ok = True
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "branch_name_deleted",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "error": err},
    }, default=str))


async def handle_delete_branch(ws, cmd: dict):
    """Delete a branch: the tail only it holds, from its tip up to the
    fork point (``SessionStore.branch_tail``), and its record.

    ``head_msg_id`` names a message on the branch; without it the
    selected branch is deleted (the terminal UI's ``/branch delete``).
    The conversation's only line is refused. Deleting the selected
    branch first switches to another branch, so the record left behind
    is never the selected one, then removes the tail and its records.
    """
    from openprogram.interfaces.server import server as _s
    session_id = cmd.get("session_id")
    head_msg_id = cmd.get("head_msg_id")
    if not head_msg_id and session_id:
        head_msg_id = await run_for_conversation(session_id, _stored_head, session_id)
    ok = False
    err = None
    deleted = 0
    new_head = None
    refusal = None
    tail = None
    owner = f"branch_delete_{uuid.uuid4().hex}"
    if not session_id or not head_msg_id:
        err = "session_id and head_msg_id required"
    else:
        try:
            from openprogram.runtime.context.session_db import default_db
            tail = await run_for_conversation(session_id, default_db().branch_tail, session_id, head_msg_id)
        except ValueError as e:
            err = str(e)
    if err is None:
        # A branch operation on the selected branch. Refused while a turn
        # writes INTO the tail about to be deleted: the selected branch's,
        # or one on another branch whose tip lies in the removed subtree.
        # A turn elsewhere keeps running.
        refusal = await run_for_conversation(session_id, 
            _s.reserve_branch_operation, session_id, owner,
            target_head=tail["start"], subtree=True,
        )
        if refusal is not None:
            err = refusal["error"]
    if err is None:
        ok, err, deleted, new_head = await run_for_conversation(session_id, 
            _delete_held_branch, _s, session_id, head_msg_id, owner)
    await ws.send_text(json.dumps({
        "type": "branch_deleted",
        "data": {"session_id": session_id, "head_msg_id": head_msg_id,
                  "ok": ok, "deleted": deleted,
                  "new_head": new_head, "error": err,
                  **({key: refusal[key] for key in ("code", "kind", "branch_id", "blocking")
                      if key in refusal} if refusal else {})},
    }, default=str))


def _delete_held_branch(_s, session_id: str, head_msg_id: str, owner: str):
    """Delete the branch ``owner`` holds and give the hold back:
    ``(ok, error, deleted, new_head)``. Runs off the event loop: every
    step takes the conversation's lock."""
    ok, err, deleted, new_head = False, None, 0, None
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        # Read again under the hold: the tree may have grown meanwhile.
        tail = db.branch_tail(session_id, head_msg_id)
        doomed = set(tail["nodes"])
        cur_head = (db.get_session(session_id) or {}).get("head_id")
        if cur_head in doomed:
            # Move off first, through an ordinary switch: the branch
            # being deleted keeps its record unselected, and the
            # delete below removes it with its tail.
            new_head = next((lf["head_msg_id"] for lf in db.list_branches(session_id)
                             if lf["head_msg_id"] not in doomed), tail["fork"])
            if not _s._set_active_head(session_id, new_head):
                raise RuntimeError("the branch changed while it was being deleted; try again")
        deleted = db.delete_branch_tail(session_id, tail["start"])
        # _set_active_head refreshed conv["messages"] for a moved HEAD;
        # the deleted rows may still sit in a cached list otherwise.
        _s._invalidate_messages(session_id)
        _s.refresh_context_stats(session_id)
        ok = True
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        _s._release_run_reservation(session_id, owner)
    return ok, err, deleted, new_head


def _attach_branch(session_id: str, target_head: str, anchor_arg: str | None,
                   anchor_session_id: str, label_override: str | None) -> dict:
    """Write the attach pointer; the result frame's data.

    Every step reads or writes a conversation under its lock, so the
    whole attach runs in a worker thread (``handle_attach_branch``)."""
    import json as _json
    import time
    import uuid

    from openprogram.interfaces.server import server as _s

    # Attach appends a row to the ANCHOR session and mark_merged mutates
    # the SOURCE session, so a run in flight on either side can race us.
    _busy = next(
        (s for s in {anchor_session_id, session_id} if _s._is_run_active(s)),
        None,
    )
    if _busy:
        return {
            "session_id": session_id,
            "anchor_session_id": anchor_session_id,
            "target_head_msg_id": target_head,
            "ok": False,
            "code": "run_active",
            "error": _s.RUN_ACTIVE_ERROR,
        }

    ok = False
    error: str | None = None
    attach_node_id: str | None = None
    anchor: str | None = None
    try:
        from openprogram.runtime.context.session_db import default_db
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
                    return {
                        "session_id": session_id,
                        "anchor_session_id": anchor_session_id,
                        "target_head_msg_id": target_head,
                        "anchor": anchor,
                        "attach_node_id": m.get("id"),
                        "ok": True,
                        "duplicate": True,
                        "error": None,
                    }
        except Exception:
            # Duplicate check is an optimisation; attaching again is harmless.
            _log.debug(
                "duplicate attach check failed for session %s", session_id, exc_info=True)
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
            # The card renders without a preview.
            _log.debug(
                "attach preview unavailable for session %s", session_id, exc_info=True)

        # Look up the source branch's current ContextCommit id so the
        # generator can expand the attach pointer into a copy of that
        # commit's items on the next turn (see
        # docs/design/context/context-attach-merge.md, scenario B). Missing /
        # absent → legacy single-item fallback path in the generator.
        source_commit_id = None
        try:
            from openprogram.state.store.session.context_commit.store import load_commit_for_head
            src_commit = load_commit_for_head(db, session_id, target_head)
            if src_commit is not None:
                source_commit_id = src_commit.id
        except Exception:
            # No commit id: the generator uses its single-item fallback.
            _log.debug(
                "source context commit unavailable for session %s", session_id, exc_info=True)

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
        db.append_message(anchor_session_id, attach_msg, advance_head=False)
        try:
            db.commit_turn(
                anchor_session_id,
                f"attach branch: {target_label or target_head[:8]}",
            )
        except Exception:
            # The attach row landed; the turn commit is bookkeeping.
            _log.warning(
                "attach turn not committed in session %s", anchor_session_id, exc_info=True)
        # Manual attach consumes the source branch — its content is
        # now embedded into the anchor lane, so the sub-branch should
        # disappear from the Branches panel (same semantics as merge
        # turn). Apply only when source == anchor (same-session
        # attach); cross-session attaches don't own the source head.
        if anchor_session_id == session_id:
            try:
                db.mark_merged(session_id, [target_head])
            except Exception:
                # The attach landed; the source branch just stays listed.
                _log.warning(
                    "attached branch not marked merged in session %s", session_id, exc_info=True)
        # The attach row landed in the anchor session's DAG, so both the
        # cached branch list and conv["messages"] are now missing it.
        # Re-pin the pre-attach head through _set_active_head to re-read
        # the branch into the mirror and drop the stale cache — without
        # this, _save_session later writes the pre-attach message list
        # back and the card vanishes until a refresh. With no pre-attach head
        # only the cache/mirror needs re-reading.
        if head_before:
            _s._set_active_head(anchor_session_id, head_before)
        else:
            _s._invalidate_messages(anchor_session_id)
        ok = True
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    if not ok:
        # Defect 6: this frame used to be the ONLY signal and nothing
        # consumed it, so a failed attach was completely silent. Log
        # server-side too so failures are diagnosable from the worker
        # log regardless of what the frontend does with the frame.
        _s._log(
            f"[attach_branch] FAILED session={session_id} "
            f"anchor={anchor_session_id} target={target_head} "
            f"anchor_head={anchor}: {error}"
        )
    return {
        "session_id": session_id,
        "anchor_session_id": anchor_session_id,
        "target_head_msg_id": target_head,
        "anchor": anchor,
        "attach_node_id": attach_node_id,
        "ok": ok,
        "error": error,
    }


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
        out: open transcripts of the anchor session get the new attach
             card from the transcript publisher.

    The attach reads and writes both conversations under their locks: it
    runs in the anchor conversation's lane, off the event loop.
    """
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

    data = await run_for_conversation(
        anchor_session_id, _attach_branch,
        session_id, target_head, anchor_arg, anchor_session_id, label_override,
    )
    await ws.send_text(json.dumps({"type": "attach_branch_result", "data": data}, default=str))


def run_branch_session(session_id: str | None, msg_id: str | None,
                       title: str | None = None, branch_id: str | None = None) -> dict:
    """Copy ``session_id`` up to ``msg_id`` into a new conversation.

    Shared by the ``branch_session`` WS action and
    ``POST /api/chat/branch_session``. Without ``msg_id`` the copy ends at
    the conversation's HEAD ("Copy conversation" in the ⋯ menu), or, while
    a turn runs, at the last finished turn before it (``copy_tip``).
    Returns the result frame's data::

        {"session_id", "msg_id", "ok", "new_session_id", "title"?,
         "error"?, "code"?}

    The copy reads the source only, so it is allowed while a turn runs
    there; the run state is read only to pick the tip when ``msg_id`` is
    omitted. On success the session list is broadcast so every sidebar
    shows the new row at once.
    """
    from openprogram.runtime.context.session_dag.branch_session import (
        BranchSessionError, branch_session, copy_tip,
    )

    session_id = (session_id or "").strip()
    msg_id = (msg_id or "").strip()
    data: dict = {"session_id": session_id or None, "msg_id": msg_id or None,
                  "ok": False, "new_session_id": None}
    if not session_id:
        data.update(error="session_id is required", code="bad_request")
        return data
    try:
        from openprogram.interfaces.server import server as _srv
        running = bool(_srv._is_run_active(session_id))
        forked = False
        if not msg_id:
            msg_id = copy_tip(session_id, running=running)
            data["msg_id"] = msg_id
        else:
            # A copy ending at the tip is a whole copy; one ending earlier
            # is a fork, which the new conversation's banner names.
            try:
                forked = msg_id != copy_tip(session_id, running=running)
            except BranchSessionError:
                forked = True
        new_id = branch_session(session_id, msg_id,
                                title=(title or "").strip() or None, forked=forked,
                                **({"branch_id": branch_id} if branch_id is not None else {}))
    except BranchSessionError as e:
        data.update(error=str(e), code=e.code)
        return data
    except Exception as e:  # noqa: BLE001 — reported to the caller
        data.update(error=f"{type(e).__name__}: {e}", code="internal")
        return data
    data.update(ok=True, new_session_id=new_id)
    try:
        from openprogram.runtime.context.session_db import default_db
        data["title"] = (default_db().get_session(new_id) or {}).get("title") or ""
    except Exception:  # noqa: BLE001 — the title is a convenience
        data["title"] = ""
    try:
        from openprogram.interfaces.server.ws_actions.session import broadcast_sessions_list
        broadcast_sessions_list()
    except Exception:  # noqa: BLE001 — the requester opens it regardless
        _log.debug(
            "sessions list broadcast failed after branching %s", new_id, exc_info=True)
    return data


async def handle_branch_session(ws, cmd: dict) -> None:
    """Copy a conversation into a NEW session up to a message (or HEAD).

    Wire format::

        in:  {"action": "branch_session", "session_id": "...",
              "msg_id": "...", "title": "..." (optional)}
        out: {"type": "branch_session_result",
              "data": {"session_id", "msg_id", "ok", "new_session_id",
                       "title"? (on success), "error"?, "code"?}}
             + a ``sessions_list`` broadcast on success.
    """
    data = await run_for_conversation(cmd.get("session_id"), 
        run_branch_session, cmd.get("session_id"), cmd.get("msg_id"), cmd.get("title"), cmd.get("branch_id"),
    )
    await ws.send_text(json.dumps(
        {"type": "branch_session_result", "data": data}, default=str))


ACTIONS = {
    "list_branches": handle_list_branches,
    "checkout_branch": handle_checkout_branch,
    "resolve_workspace_alignment": handle_resolve_workspace_alignment,
    "rename_branch": handle_rename_branch,
    "auto_name_branch": handle_auto_name_branch,
    "delete_branch_name": handle_delete_branch_name,
    "delete_branch": handle_delete_branch,
    "attach_branch": handle_attach_branch,
    "branch_session": handle_branch_session,
}
