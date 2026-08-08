"""send_message — the single branch-to-branch communication primitive.

Deliver a message to a branch → trigger that branch to run one turn →
its reply auto-returns to the sender. Three usages via ``to``:

  * ``"new"``            — create a fresh branch (new session) from ROOT,
                           deliver the message, run it. (spawn / new chat)
  * ``"new:sid:msg_id"`` — fork a new branch off a node, deliver, run.
  * ``"sid:head"``       — deliver to an existing branch.

Async by default (``wait=False``): returns immediately with a delivery id;
the target runs in the background and its reply comes back to the sender
automatically (the task runner's followup). ``wait=True`` blocks for the
reply.

Design: docs/design/runtime/agent-collaboration.md. This file is C1 —
the core path for ``to="new"`` (spawn usage). Existing-branch /
cross-session / synthesis / robustness land in later steps.
"""
from __future__ import annotations

import contextvars

from openprogram.functions._runtime import function


# Depth of the current spawn chain (A→B→C…). Each send_message that
# spawns increments it for the child turn; when it reaches MAX_SPAWN_DEPTH
# further spawns are refused — the guard against A↔B / runaway recursion
# (design §5.1). Set by the runner on the child turn (cross-thread) and by
# the sync path inline.
_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "send_message_spawn_depth", default=0,
)
MAX_SPAWN_DEPTH = 8


def current_spawn_depth() -> int:
    return _spawn_depth.get()


def set_spawn_depth(depth: int):
    """Bind the spawn depth for the current execution context (used by the
    task runner when starting a spawned child turn). Returns the token."""
    return _spawn_depth.set(depth)


_DESCRIPTION = (
    "Branch-to-branch communication: deliver a message to a branch, run "
    "one turn there, and (by default, async) have its reply come back to "
    "you automatically. ONE tool for spawning sub-agents, messaging other "
    "branches, and synthesizing across branches — chosen by `to`:\n"
    "\n"
    "  to=\"new\" (DEFAULT): create a fresh branch and run `message` "
    "in it — i.e. spawn a sub-agent / open a new line of work. The new "
    "branch sees ONLY `message` (a clean worker); pack what it needs into "
    "the message. Want several? call this several times — they run in "
    "parallel, each returning to you when done.\n"
    "  to=\"new:SID:MSG_ID\": fork a new branch off an existing node "
    "(it inherits the chain up to that node), then run `message`.\n"
    "  to=\"SID:HEAD\": deliver `message` to an existing branch and "
    "trigger it to respond. If the target is busy running a turn, the "
    "message is queued and processed when that turn ends.\n"
    "  to=\"<branch name>\": address a named branch directly (exact "
    "name, or a unique prefix — see list_branches for names).\n"
    "\n"
    "When you RECEIVE a message from another branch (it starts with "
    "\"[message from SID:HEAD]\"), replying is optional — the message is "
    "already delivered; reply only when you have something substantive "
    "to add, and when unsure, don't reply.\n"
    "\n"
    "wait=False (DEFAULT): returns a delivery id immediately; you are NOT "
    "blocked — keep working. The target's reply is delivered back to you "
    "as a new message automatically when it finishes. wait=True: block "
    "and return the reply text directly.\n"
    "\n"
    "Use this to offload sub-tasks, run parallel explorations, or hand a "
    "message to another agent/branch."
)


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Current (session_id, turn_id, agent_id) for anchoring the spawn.

    Reads the dispatcher's ContextVars first (same as the ``task`` tool).
    ``_current_turn_id`` isn't always bound on every execution path (e.g.
    a followup turn, or some sub-call stacks), so when it's missing we
    fall back to the session's current head — that head IS a valid parent
    anchor for the new branch, which is what callers actually need. This
    is what fixes the "no active parent turn" the model sometimes hit."""
    try:
        from openprogram.agent.run_control import _current_session_id
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    try:
        from openprogram.store import _current_turn_id
        aid = _current_turn_id.get()
    except Exception:
        aid = None
    agent_id = None
    if sid:
        try:
            from openprogram.agent.session_db import default_db
            sess = default_db().get_session(sid) or {}
            agent_id = sess.get("agent_id")
            if not aid:
                # Fall back to the session head as the parent anchor.
                aid = sess.get("head_id")
        except Exception:
            agent_id = None
    return sid, aid, agent_id


def _emit_branch_ui(session_id: str, kind: str, peer: str, text: str) -> None:
    """Push a UI frame so the given session's chat stream shows a branch
    communication line. kind ∈ {"sent","replied"}. Best-effort."""
    try:
        from openprogram.events import emit_ws_frame
        summary = (text or "").replace("\n", " ").strip()
        if len(summary) > 120:
            summary = summary[:119] + "…"
        emit_ws_frame({
            "type": "branch_message",
            "data": {
                "session_id": session_id,
                "kind": kind,        # "sent" → 我发给X；"replied" → X回复了
                "peer": peer,        # 对端分支标识
                "summary": summary,
            },
        })
    except Exception:
        pass


def _gather_sources(sources: list[str] | None) -> str:
    """Pull each source branch's tip text and wrap it in a labelled block,
    so the target model reads them and synthesizes. Each source is
    ``"SID:HEAD"`` (or ``"SID"`` → that session's current head).

    Returns the assembled block string (empty if no usable sources).
    """
    if not sources:
        return ""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.internals._merge import _peer_final_text
    store = default_db()
    blocks: list[str] = []
    for raw in sources:
        s = (raw or "").strip()
        if not s:
            continue
        ssid, _, shead = s.partition(":")
        ssid = ssid.strip()
        shead = shead.strip() or None
        if not ssid:
            continue
        try:
            text, _hid = _peer_final_text(store, ssid, shead)
        except Exception:
            text = ""
        text = (text or "").strip()
        if not text:
            text = "(this branch has no readable content)"
        blocks.append(f'<branch source="{s}">\n{text}\n</branch>')
    if not blocks:
        return ""
    return (
        "下面是几条分支的内容，请阅读后综合，再回应本条消息：\n\n"
        + "\n\n".join(blocks)
        + "\n\n---\n\n"
    )


def sender_header(sender_session_id: str, sender_msg_id: str) -> str:
    """The receipt header prepended to every message delivered to an
    existing branch (direct and queued-consume paths), so the receiver
    knows who sent it and how to answer."""
    src = f"{sender_session_id}:{sender_msg_id}"
    return (
        f"[message from {src}] To reply, use send_message(to=\"{src}\"). "
        "Replying is optional — this message is already delivered; if you "
        "have nothing substantive to add, do not reply.\n\n"
    )


def _resolve_branch_by_name(name: str) -> tuple[str, object]:
    """Resolve a branch NAME into a (session_id, head_id) target.

    Exact match wins; a unique prefix is accepted next. The current
    session's branches are searched first, then every other session.
    Returns one of:
      ("ok", (session_id, head_id))
      ("ambiguous", [(name, session_id, head_id), ...])
      ("none", None)
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    needle = (name or "").strip()
    if not needle:
        return "none", None
    try:
        from openprogram.agent.run_control import _current_session_id
        cur = _current_session_id.get(None)
    except Exception:
        cur = None
    sids: list[str] = []
    if cur:
        sids.append(cur)
    try:
        for row in db.list_sessions(limit=200) or []:
            sid = row.get("id")
            if sid and sid not in sids:
                sids.append(sid)
    except Exception:
        pass
    candidates: list[tuple[str, str, str]] = []
    for sid in sids:
        try:
            branches = db.list_branches(sid) or []
        except Exception:
            continue
        for b in branches:
            bname = (b.get("name") or "").strip()
            head = b.get("head_msg_id")
            if bname and head:
                candidates.append((bname, sid, head))
    exact = [c for c in candidates if c[0] == needle]
    if len(exact) == 1:
        return "ok", (exact[0][1], exact[0][2])
    if len(exact) > 1:
        return "ambiguous", exact
    prefix = [c for c in candidates if c[0].startswith(needle)]
    if len(prefix) == 1:
        return "ok", (prefix[0][1], prefix[0][2])
    if len(prefix) > 1:
        return "ambiguous", prefix
    return "none", None


def _parse_to(to: str) -> tuple[str, str | None, str | None]:
    """Parse the ``to`` arg into (kind, session_id, fork_msg_id).

    kind ∈ {"new", "fork", "existing"}:
      * "new"            → ("new", None, None)
      * "new:SID:MSG_ID" → ("fork", SID, MSG_ID)
      * "SID:HEAD"       → ("existing", SID, HEAD)
    """
    t = (to or "new").strip()
    if t == "new":
        return "new", None, None
    if t.startswith("new:"):
        rest = t[len("new:"):]
        sid, _, msg = rest.partition(":")
        return "fork", sid or None, (msg or None)
    sid, sep, head = t.partition(":")
    return "existing", sid or None, (head or None)


def _send_message_impl(
    message: str,
    to: str = "new",
    sources: list[str] | None = None,
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Implementation body, pulled out of the @function binding so tests
    can drive it with their own ContextVars."""
    from openprogram.events import emit_safe

    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[send_message error] no active parent turn — must be called "
            "from inside an assistant turn (the dispatcher sets the session "
            "+ turn ContextVars on entry)."
        )

    # Depth guard (§5.1): refuse spawning past MAX_SPAWN_DEPTH so A↔B /
    # runaway recursion can't blow up. The reply-followup inherits the
    # same depth, so back-and-forth also counts toward it.
    depth = current_spawn_depth()
    if depth >= MAX_SPAWN_DEPTH:
        return (
            f"[send_message refused] spawn depth {depth} reached the max "
            f"({MAX_SPAWN_DEPTH}). This chain is too deep — finish the work "
            "here instead of delegating further."
        )

    chosen_agent = (agent_id or "").strip() or parent_agent or "main"
    kind, tgt_sid, fork_msg = _parse_to(to)

    # Resolve target into (run_session, branch_from, is_new):
    #   new      → fresh root in current session
    #   fork     → fork off a node (inherit that chain)
    #   existing → deliver onto an existing branch = run one more turn off
    #              its head (the branch "continues" with the message)
    if kind == "existing":
        run_session = tgt_sid or sid
        branch_from = fork_msg  # the branch head to continue from
        is_new = False
        from openprogram.agent.session_db import default_db
        db = default_db()
        # Not valid SID:HEAD syntax (missing head, or the SID part is not
        # a session)? Treat the whole `to` as a branch NAME: exact match
        # first, unique prefix next (see _resolve_branch_by_name).
        if not branch_from or db.get_session(run_session) is None:
            status, resolved = _resolve_branch_by_name(to)
            if status == "ok":
                run_session, branch_from = resolved  # type: ignore[misc]
            elif status == "ambiguous":
                lines = "\n".join(
                    f"  «{n}» → {s}:{h}" for n, s, h in resolved  # type: ignore[union-attr]
                )
                return (
                    f"[send_message error] branch name {to!r} matches "
                    f"several branches — use the exact SID:HEAD target:\n"
                    f"{lines}"
                )
            elif not branch_from and db.get_session(run_session) is not None:
                return (
                    "[send_message error] to=\"SID:HEAD\" needs the branch "
                    "head after the colon (see list_branches for ready targets)."
                )
            else:
                return (
                    f"[send_message error] target {to!r} not found — it is "
                    "neither a session:head target nor a branch name (see "
                    "list_branches / list_sessions)."
                )
        # Self-target guard: messaging your own current turn is a direct loop.
        if run_session == sid and branch_from == aid:
            return (
                "[send_message refused] target is your own current turn — "
                "that's a direct loop. Pick a different branch or use to=new."
            )
    elif kind == "fork":
        run_session = tgt_sid or sid
        branch_from = fork_msg
        is_new = True
        if not branch_from:
            return (
                "[send_message error] to=\"new:SID:MSG_ID\" needs a "
                "fork node id after the second colon."
            )
    else:  # "new" — fresh branch in the current session repo (new root)
        run_session = sid
        branch_from = None
        is_new = True

    # Synthesis: prepend each source branch's content as a labelled block,
    # so the target model reads them and synthesizes (C5).
    delivery_body = _gather_sources(sources) + message
    # Deliveries to an EXISTING branch carry a sender-receipt header so
    # the receiver knows who sent it and that replying is optional. New
    # branches (spawn/fork) get the bare message — they are workers, not
    # correspondents.
    if kind == "existing":
        delivery_message = sender_header(sid, aid) + delivery_body
    else:
        delivery_message = delivery_body

    # Busy target → inbox (design §5.4: don't interrupt, don't drop —
    # queue). Only cross-session sends can race another turn: a
    # same-session send runs inside the sender's own turn, which is the
    # very token is_turn_running would see.
    if kind == "existing" and run_session != sid:
        from openprogram.agent.run_control import is_turn_running
        if is_turn_running(run_session):
            from openprogram.agent import inbox
            try:
                status = inbox.enqueue(
                    run_session,
                    message=delivery_body,
                    sender_session_id=sid,
                    sender_msg_id=aid,
                    sender_agent_id=parent_agent,
                    agent_id=chosen_agent,
                    spawn_depth=depth,
                    target_head_id=branch_from,
                )
            except Exception as e:  # noqa: BLE001
                return f"[send_message error] {type(e).__name__}: {e}"
            if status == "duplicate":
                return (
                    "[send_message] duplicate message ignored — an "
                    "identical message from you is already queued for "
                    "this target (sent within the last 60s)."
                )
            # Race window: the target may have finished between the busy
            # check and the enqueue — drain now so the message doesn't
            # sit a whole extra turn.
            if not is_turn_running(run_session):
                try:
                    inbox.drain(run_session)
                except Exception:
                    pass
            return (
                f"[queued] target branch {run_session}:{branch_from} is "
                "busy running a turn. Your message is queued; it will be "
                "processed when the target's current turn ends and its "
                "reply will come back to you automatically."
            )

    emit_safe(
        "branch.message_sent",
        "agent",
        {
            "from": f"{sid}:{aid}",
            "to": f"{run_session}:{branch_from}" if branch_from else run_session,
            "is_new": is_new,
            "sources": sources or [],
        },
    )
    # Also push a UI frame so the sender's session shows a "message sent"
    # line in its chat stream (front-end useWS handles `branch_message`).
    _to_label = f"{run_session}:{branch_from}" if branch_from else f"{run_session} (new branch)"
    _emit_branch_ui(sid, "sent", _to_label, message)

    # Async (default): submit to the task runner. It runs the new branch
    # on a worker thread, writes the attach pointer, and dispatches a
    # followup back to THIS session when done — that followup IS the
    # auto-return of the reply to the sender.
    if not wait:
        try:
            from openprogram.agent.sub_agent_run import run_agent_turn_async
            task_id = run_agent_turn_async(
                session_id=run_session,
                prompt=delivery_message,
                agent_id=chosen_agent,
                branch_from=branch_from,
                context_mode="inherit" if branch_from else "clean",
                label=message[:60],  # Stage-1 placeholder name (Stage-2 LLM refines later)
                subject=message[:60],
                description=delivery_message,
                caller_msg_id=aid,
                caller_session_id=sid,  # reply returns to the sender
                spawn_depth=depth + 1,  # child inherits depth+1 (loop guard)
            )
        except Exception as e:  # noqa: BLE001
            return f"[send_message error] {type(e).__name__}: {e}"
        return (
            f"[delivered, running async] delivery_id={task_id}\n"
            "The target branch is running; its reply will come back to you "
            "automatically when it finishes. You are not blocked — continue."
        )

    # Sync: run inline, write the attach pointer, return the reply text.
    # Bind depth+1 for the inline child turn (same execution context).
    _tok = set_spawn_depth(depth + 1)
    try:
        from openprogram.agent.sub_agent_run import (
            run_agent_turn,
            write_attach_pointer_for_spawn,
        )
        result = run_agent_turn(
            session_id=run_session,
            prompt=delivery_message,
            agent_id=chosen_agent,
            branch_from=branch_from,
            label=message[:60],  # Stage-1 placeholder name
            # New branch (branch_from=None) → root's caller = spawning node,
            # so it's an explicit spawn, not seq-stitched into a sibling.
            spawn_caller=aid if branch_from is None else None,
            # A spawn/fork in the SENDER's own session must not steal
            # its head; a cross-session send continues the target's
            # conversation and advances theirs as usual.
            advance_head=(run_session != sid),
        )
    except Exception as e:  # noqa: BLE001
        return f"[send_message error] {type(e).__name__}: {e}"
    finally:
        _spawn_depth.reset(_tok)

    try:
        write_attach_pointer_for_spawn(
            session_id=run_session,
            caller_msg_id=aid,
            result=result,
            label=message[:60],  # match the branch's Stage-1 placeholder name
            prompt=message,
            chosen_agent=chosen_agent,
        )
    except Exception:
        pass

    emit_safe(
        "branch.message_replied",
        "agent",
        {
            "from": run_session,
            "to": f"{sid}:{aid}",
            "is_error": bool(result.failed or result.error),
        },
    )
    _emit_branch_ui(sid, "replied", run_session, result.final_text or "")

    if result.error and not result.final_text:
        return f"[send_message error: head={result.head_id}] {result.error}"
    out = _clip_result(result.final_text or "(target branch returned no text)")
    if result.error:
        out = f"{out}\n\n[send_message warning] {result.error}"
    return f"{out}\n\n[branch {run_session}:{result.head_id or '?'}]"


_MAX_RESULT_CHARS = 30_000


def _clip_result(text: str) -> str:
    """Truncate an oversized reply head+tail and save the full text to a
    file, returning a path the caller can read (§5.6) — so a huge branch
    reply doesn't blow up the sender's context."""
    s = text or ""
    if len(s) <= _MAX_RESULT_CHARS:
        return s
    import tempfile
    import os
    head = s[: _MAX_RESULT_CHARS // 2]
    tail = s[-_MAX_RESULT_CHARS // 2:]
    try:
        fd, path = tempfile.mkstemp(prefix="branch_reply_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(s)
    except Exception:
        path = "(could not write file)"
    return (
        f"{head}\n\n... [truncated {len(s)} chars; full reply saved to "
        f"{path}] ...\n\n{tail}"
    )


@function(
    name="send_message",
    description=_DESCRIPTION,
    toolset=["core"],
)
def send_message(
    message: str,
    to: str = "new",
    sources: list[str] | None = None,
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Deliver a message to a branch, run it, get the reply back."""
    return _send_message_impl(
        message=message,
        to=to,
        sources=sources,
        agent_id=agent_id,
        wait=wait,
    )
