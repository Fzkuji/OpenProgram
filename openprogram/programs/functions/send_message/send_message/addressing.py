"""Target addressing for send_message: parse the ``to`` argument and
resolve branch names into ``(session_id, head_id)`` targets."""
from __future__ import annotations


def _parse_to(to: str) -> tuple[str, str | None, str | None]:
    """Parse the ``to`` arg into (kind, session_id, head_id).

    kind ∈ {"existing", "spawn_syntax"}:
      * "SID:HEAD"          → ("existing", SID, HEAD)
      * "new" / "new:…"     → ("spawn_syntax", None, None) — the removed
                              spawn addressing; the caller reports "use
                              the agent tool" for these.
    """
    t = (to or "").strip()
    if t == "new" or t.startswith("new:"):
        return "spawn_syntax", None, None
    sid, _, head = t.partition(":")
    return "existing", sid or None, (head or None)


def _normalize_existing_target(
    session_id: str, node_id: str
) -> tuple[str, object]:
    """Snap an existing-branch target node onto its branch's CURRENT tip.

    ``to="SID:HEAD"`` names a BRANCH (via any node on it), not a fork
    point — the branch may have run more turns since the sender saw its
    head, so delivering onto the given node verbatim would fork a new
    branch off history instead of continuing the conversation. Forking
    off a node is the agent tool's job (``agent(start_from="SID:MSG_ID")``).

    Snapping applies to LIVE branches only. A merged head names a
    branch a merge retired, and that branch keeps its own identity:
    resolving it onto the live branch that absorbed it would make
    archive_agent / read_conversation act on a different agent, so a
    merged head resolves to itself.

    Returns one of:
      ("ok", tip_id)                      — deliver onto this tip
      ("ambiguous", [(name, tip_id), …])  — node is a shared ancestor of
                                            several branches
      ("none", None)                      — node is on no branch of the
                                            session (or doesn't exist)
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    tips = db.list_branches(session_id) or []
    # Fast path: the node already IS a branch tip.
    for t in tips:
        if t.get("head_msg_id") == node_id:
            return "ok", node_id
    # A retired branch's head is still that branch's address.
    try:
        if node_id in db.merged_heads(session_id):
            return "ok", node_id
    except Exception:
        pass
    containing: list[tuple[str, str]] = []
    for t in tips:
        tip = t.get("head_msg_id")
        if not tip:
            continue
        try:
            chain = db.get_branch(session_id, tip) or []
        except Exception:
            continue
        if any(m.get("id") == node_id for m in chain):
            containing.append((t.get("name") or "", tip))
    if len(containing) == 1:
        return "ok", containing[0][1]
    if len(containing) > 1:
        return "ambiguous", containing
    return "none", None


def resolve_existing_target(
    to: str, current_session_id: str, *, allow_archived: bool = False,
) -> tuple[str, object]:
    """Resolve ``to`` (a ``SID:HEAD`` address or a branch name) onto an
    existing branch's CURRENT tip. Shared by ``send_message`` and the
    ``agent`` tool's ``to=`` dispatch — one addressing behavior, two
    callers.

    An archived branch resolves but is refused (one guard for every
    delivery path): archiving removes the branch's right to be
    disturbed, not its history. ``allow_archived=True`` skips that
    guard — ``archive_agent`` uses it to address archived branches.

    Returns ``("ok", (session_id, tip_id))`` or ``("error", body)``
    where ``body`` is the message without a tool prefix (each caller
    prepends its own ``[send_message error]`` / ``[agent error]`` tag).
    """
    from openprogram.agent.session_db import default_db
    db = default_db()
    _, tgt_sid, head = _parse_to(to)
    run_session = tgt_sid or current_session_id
    branch_from = head
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
            return "error", (
                f"branch name {to!r} matches several branches — use the "
                f"exact SID:HEAD target:\n{lines}"
            )
        elif not branch_from and db.get_session(run_session) is not None:
            return "error", (
                "to=\"SID:HEAD\" needs the branch head after the colon "
                "(see list_agents for ready targets)."
            )
        else:
            return "error", (
                f"target {to!r} not found — it is neither a session:head "
                "target nor a branch name (see list_agents)."
            )
    # SID:HEAD names a BRANCH, not a fork point: snap the given node
    # onto that branch's CURRENT tip so the delivery continues the
    # conversation instead of forking off a stale head.
    status, norm = _normalize_existing_target(run_session, branch_from)
    if status == "ok":
        if not allow_archived:
            try:
                archived = bool(
                    db.get_branch_meta(run_session, norm).get("archived")
                )
            except Exception:
                archived = False
            if archived:
                return "error", (
                    f"agent {run_session}:{norm} is archived — it no "
                    "longer accepts messages or tasks. Its history is "
                    "still readable with read_conversation, and "
                    f"agent(start_from=\"{run_session}:MSG_ID\") can fork it."
                )
        return "ok", (run_session, norm)
    if status == "ambiguous":
        lines = "\n".join(
            f"  «{n or '(unnamed)'}» → {run_session}:{h}" for n, h in norm  # type: ignore[union-attr]
        )
        return "error", (
            f"node {branch_from!r} is a shared ancestor of several "
            "branches — address the branch you mean by its current tip "
            f"(see list_agents):\n{lines}"
        )
    return "error", (
        f"target {to!r} not found — the node is on no branch of session "
        f"{run_session} (see list_agents for ready targets)."
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
        for row in db.list_sessions(limit=200, include_archived=True) or []:
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
