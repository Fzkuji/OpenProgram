"""Target addressing for send_message: parse the ``to`` argument and
resolve branch names into ``(session_id, head_id)`` targets."""
from __future__ import annotations
import logging

_log = logging.getLogger(__name__)


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
    from openprogram.runtime.context.session_db import default_db
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
        # Merged heads are an extra lookup; the containing-branch search still runs.
        _log.debug("merged heads unavailable session=%s", session_id, exc_info=True)
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


def name_key(name: str) -> str:
    """A name with case, spaces and punctuation folded away, so a name is
    reached however it is spelled: with spaces as written, or with the
    underscores older agent names were stored with."""
    import re
    return "_".join(re.split(r"[\W_]+", (name or "").casefold())).strip("_")


def agent_tips(session_id: str) -> list[tuple[str, str]]:
    """The agents spawned in this session, as ``(label, tip)``: the name
    the ``agent`` call gave it and the current tip of its branch. A
    finished agent's branch is retired from the branch list and may be
    renamed by the auto-namer, yet the name it was spawned with stays
    its address — as in Claude Code, a finished agent can be messaged
    again by name. Newest first; archived agents are left out."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.jobs.store import list_jobs
    db = default_db()
    try:
        jobs = [j for j in list_jobs(session_id)
                if j.creates_agent and j.label and j.head_id]
    except Exception:
        return []
    if not jobs:
        return []
    try:
        live = [b.get("head_msg_id") for b in db.list_branches(session_id) or []
                if b.get("head_msg_id")]
    except Exception:
        live = []
    chains: dict[str, set[str]] = {}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for job in sorted(jobs, key=lambda j: j.created_at or 0, reverse=True):
        tip = job.head_id
        # The branch may have run more turns since the job ended (a
        # message delivered to it): its tip is the live branch that
        # contains the job's head.
        for t in live:
            if t not in chains:
                try:
                    chains[t] = {m.get("id") for m in db.get_branch(session_id, t) or []}
                except Exception:
                    chains[t] = set()
            if job.head_id in chains[t]:
                tip = t
                break
        if tip in seen:
            continue
        try:
            if db.get_branch_meta(session_id, tip).get("archived"):
                continue
        except Exception:
            # Unknown archive state: keep the tip as a candidate.
            _log.debug("branch meta unavailable session=%s tip=%s", session_id, tip, exc_info=True)
        seen.add(tip)
        out.append((str(job.label), str(tip)))
    return out


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
    from openprogram.runtime.context.session_db import default_db
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
    from openprogram.runtime.context.session_db import default_db
    db = default_db()
    needle = (name or "").strip()
    if not needle:
        return "none", None
    try:
        from openprogram.runtime.execution.run_control.binding import (
            _current_session_id,
        )
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
        # The session list only widens the search; the current session is still tried.
        _log.debug("session list unavailable for addressing", exc_info=True)
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
    tips = agent_tips(cur) if cur and not exact else []
    if not exact:
        # The name an agent was spawned with (its branch may have been
        # renamed since, or retired from the branch list when it ended).
        exact = [(label, cur, tip) for label, tip in tips if label == needle]
    if not exact and name_key(needle):
        # Spelled another way: "Spell the word harbor" and the older
        # identifier form "Spell_the_word_harbor" are one name.
        key = name_key(needle)
        exact = [c for c in candidates if name_key(c[0]) == key] or [
            (label, cur, tip) for label, tip in tips if name_key(label) == key]
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


# --- conversations and saved Agents ------------------------------------
#
# A conversation by its id, ``SID:HEAD`` on its current line, or exact
# title; a saved Agent by name or id reaches its one conversation. This was
# the always-on hand-off's addressing; send_message took it over with the
# hand-off's job (design: agent-collaboration.html#tools).

_TITLE_SCAN = 500


class AddressRefused(ValueError):
    """``to`` names no single conversation; the message says what does."""


def _title(session: dict) -> str:
    return str(session.get("title") or "").strip() or "Untitled conversation"


def _listed(rows: list[dict]) -> str:
    return ", ".join(f"“{_title(row)}” ({row['id']})" for row in rows[:10])


def _takes_messages(session: dict | None) -> dict:
    """``session`` when another agent may send it work: not archived,
    imported, a sub-agent's or a Try chat."""
    from openprogram.runtime.conversation.always_on.runtime import owner_conversation
    if not owner_conversation(session):
        raise AddressRefused("That conversation cannot take messages: it is archived, "
                             "imported, a sub-agent's or a Try chat.")
    return session  # type: ignore[return-value]


def _at_head(session: dict, head: str, to: str) -> dict:
    """``SID:HEAD``: the conversation, when ``head`` is on its current line."""
    from openprogram.runtime.context.session_db import default_db
    target = _takes_messages(session)
    if not any(row.get("id") == head for row in default_db().get_branch(target["id"]) or []):
        raise AddressRefused(f"{to!r} is not on the current line of “{_title(target)}”. Pass "
                             "its id for that line, or a branch address from list_agents.")
    return target


def agents_named(to: str) -> list:
    """Saved Agents whose id or name is ``to`` (any case)."""
    from openprogram.runtime.agent_registry import manager
    wanted = to.casefold()
    return [spec for spec in manager.list_all()
            if wanted in (str(spec.id).casefold(), str(spec.name or "").strip().casefold())]


def _your_conversations(sender_id: str, rows: list[dict]) -> str:
    known = "; ".join([f"{_title(row)} ({row['id']})" for row in rows if row["id"] != sender_id][:20])
    return (f"Your conversations: {known or 'none'}. "
            "list_agents scope=\"all\" shows every Agent's conversations.")


def _conversation_of_agents(sender_id: str, agents: list, everyone: list[dict]) -> dict:
    """The one conversation, other than the sender, of the Agent ``to`` names."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on.runtime import owner_conversation
    db = default_db()
    # A list row can be older than the conversation's Try flag.
    rows = [row for spec in agents for row in db.list_sessions(limit=_TITLE_SCAN, agent_id=spec.id)
            if owner_conversation(row) and row["id"] != sender_id
            and owner_conversation(db.get_session(row["id"]))]
    if len(rows) == 1:
        return rows[0]
    name = str(agents[0].name or agents[0].id)
    if rows:
        raise AddressRefused(f"Agent “{name}” has several conversations: {_listed(rows)}. "
                             "Pass the id of the one that should do this.")
    raise AddressRefused(f"Agent “{name}” has no other conversation to send this to, and only the "
                         f"user creates conversations. {_your_conversations(sender_id, everyone)}")


def resolve_conversation(sender_id: str, to: str) -> dict:
    """The conversation ``to`` names, in this order: its id, ``SID:HEAD``,
    its exact title (any case), then a saved Agent's id or name (any case).
    Raises :class:`AddressRefused` naming the choices."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on.runtime import owner_conversation
    to = (to or "").strip()
    if not to:
        raise AddressRefused("Name who to send this to: a conversation's id or title, "
                             "an Agent's name or id, or SID:HEAD from list_agents scope=\"all\".")
    db = default_db()
    session = db.get_session(to)
    if session is not None:
        return _takes_messages(session)
    conversation_id, colon, head = to.partition(":")
    addressed = db.get_session(conversation_id) if colon and head else None
    if addressed is not None:
        return _at_head(addressed, head, to)
    rows = [row for row in db.list_sessions(limit=_TITLE_SCAN) if owner_conversation(row)]
    matches = [row for row in rows if _title(row).casefold() == to.casefold()
               and owner_conversation(db.get_session(row["id"]))]
    if len(matches) == 1:
        return matches[0]
    if matches:
        listed = ", ".join(f"{row['id']}" for row in matches[:10])
        raise AddressRefused(f"Several conversations are titled {to!r} ({listed}); pass the id instead.")
    agents = agents_named(to)
    if agents:
        return _conversation_of_agents(sender_id, agents, rows)
    raise AddressRefused(f"No conversation is titled {to!r} and no Agent has that name or id. "
                         f"{_your_conversations(sender_id, rows)}")
