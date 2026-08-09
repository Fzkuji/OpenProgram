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
