"""Target addressing for send_message: parse the ``to`` argument and
resolve branch names into ``(session_id, head_id)`` targets."""
from __future__ import annotations


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


def _normalize_existing_target(
    session_id: str, node_id: str
) -> tuple[str, object]:
    """Snap an existing-branch target node onto its branch's CURRENT tip.

    ``to="SID:HEAD"`` names a BRANCH (via any node on it), not a fork
    point — the branch may have run more turns since the sender saw its
    head, so delivering onto the given node verbatim would fork a new
    branch off history instead of continuing the conversation. Explicit
    forking has its own syntax (``to="new:SID:MSG"``).

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
