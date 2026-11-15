"""The ``set_conversation_channel`` action.

Picking a channel account on a chat ties that chat to the account's
conversation both ways. The chat's own meta records the channel, account
and peer (the sidebar prefix, the chip, the per-peer ownership check), and a
session alias (``runtime/agent_registry/session_aliases``) makes inbound
routing deliver that peer's messages into this chat instead of the
session the binding's ``session_scope`` would pick. Every assistant reply
in the chat goes out through the same account: replies to those messages
from the channel's inbound path, every other turn's (typed in the App,
always-on, scheduled) from ``runtime/conversation/bound_reply``.

The peer is the one the client sent, or else the account's owner chat
(WeChat: the user who scanned the bot's QR code). A platform with no
owner chat and no peer from the client records the binding only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger(__name__)


# update_session skips None values, so a binding is cleared with empty
# strings (every reader treats them as unset).
_CLEARED = {"channel": "", "account_id": "", "peer": "", "peer_display": ""}


@dataclass
class ChannelBindResult:
    ok: bool = False
    error: Optional[str] = None
    peer: Optional[str] = None
    evicted: list[str] = field(default_factory=list)


def _owner_peer(channel: str, account_id: str) -> str:
    try:
        from openprogram.interfaces.channels import accounts as _accounts
        return _accounts.owner_peer_id(channel, account_id)
    except Exception:
        _log.warning("owner peer of %s:%s not read", channel, account_id, exc_info=True)
        return ""


def _agent_for(_s, session_id: str) -> str:
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id) or {}
    agent_id = conv.get("agent_id")
    if not agent_id:
        from openprogram.interfaces.server import persistence as _p
        agent_id = _p.resolve_agent_for_conv(session_id)
    return agent_id or _s._default_agent_id()


def _peers_held(_s, db, session_id: str, channel: str, account_id: str) -> set[str]:
    """The peers of this account whose conversation ``session_id`` holds.

    Empty when none is known, or when a catch-all alias gives the chat the
    whole account.
    """
    from openprogram.runtime.agent_registry import session_aliases as _aliases
    from openprogram.runtime.conversation.bound_reply import reply_target

    aliased = {
        str((row.get("peer") or {}).get("id") or "")
        for row in _aliases.list_for_session(session_id)
        if row.get("channel") == channel and row.get("account_id") == account_id
    }
    if "*" in aliased:
        return set()
    aliased.discard("")
    if aliased:
        return aliased
    session = db.get_session(session_id)
    if session is None:
        with _s._sessions_lock:
            session = dict(_s._sessions.get(session_id) or {})
    target = reply_target(
        session_id, {**session, "channel": channel, "account_id": account_id},
    )
    return {target.peer_id} if target else set()


def _record_a_held_peer(
    _s, db, session_id: str, channel: str, account_id: str, taken: str, held: set[str],
) -> None:
    """``session_id`` stays on the account but lost ``taken``: if its meta
    still names that peer, name the first peer it holds, the one its
    replies go to. Questions relayed to the channel read this meta."""
    from openprogram.runtime.agent_registry import session_aliases as _aliases

    remaining = next((
        row["peer"] for row in _aliases.list_for_session(session_id)
        if row["channel"] == channel and row["account_id"] == account_id
        and row["peer"]["id"] in held and row["peer"]["id"] != taken
    ), None)
    if remaining is None:
        return
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id)
        if conv is not None and conv.get("peer") == taken:
            conv["peer"] = remaining["id"]
    try:
        recorded = (db.get_session(session_id) or {}).get("peer")
        if isinstance(recorded, dict):
            recorded = recorded.get("id")
        if recorded == taken:
            db.update_session(session_id, peer=dict(remaining), peer_display="")
    except Exception as ex:
        _s._log(f"[set_conversation_channel] repoint {session_id} db: {ex}")


def bind_conversation_channel(
    _s,
    session_id: str,
    channel: Optional[str],
    account_id: Optional[str],
    peer: Optional[str] = None,
    peer_display: Optional[str] = None,
) -> ChannelBindResult:
    """Bind ``session_id`` to ``(channel, account_id)``, or unbind it with
    no channel.

    Each peer of an account has one owner chat: taking that peer evicts
    its prior owner back to local, together with the owner's aliases on
    that account. Chats holding other peers of the account keep theirs.
    When either side has no known peer, it cannot be told apart from the
    other, so the account as a whole has one owner.
    The chat's previous aliases are dropped and, when a peer is known, one
    alias for it is written (replacing any other chat's alias for that
    peer), so exactly one chat receives that peer's messages.
    """
    from openprogram.runtime.agent_registry import session_aliases as _aliases
    from openprogram.runtime.context.session_db import default_db

    result = ChannelBindResult()
    ch = (channel or "").strip().lower() or None
    acct = (account_id or "").strip() or None if ch else None
    peer = (peer or "").strip() or None if ch else None
    if ch and acct is None:
        acct = "default"
    db = default_db()

    if ch:
        db_owners = set(db.sessions_with_binding(ch, acct))
        with _s._sessions_lock:
            mem_owners = {
                oid for oid, o in _s._sessions.items()
                if o.get("channel") == ch and o.get("account_id") == acct
            }
        wanted_peer = peer or _owner_peer(ch, acct)
        for oid in (db_owners | mem_owners) - {session_id}:
            held = _peers_held(_s, db, oid, ch, acct)
            if wanted_peer and held - {wanted_peer}:
                # It talks to someone else on this account.
                if wanted_peer in held:
                    _record_a_held_peer(_s, db, oid, ch, acct, wanted_peer, held)
                continue
            with _s._sessions_lock:
                other = _s._sessions.get(oid)
                if other is not None:
                    other["channel"] = None
                    other["account_id"] = None
                    other["peer"] = None
                    other["peer_display"] = None
            try:
                if db.get_session(oid) is not None:
                    db.update_session(oid, **_CLEARED)
            except Exception as ex:
                _s._log(f"[set_conversation_channel] evict {oid} db: {ex}")
            try:
                _aliases.detach_session(oid, channel=ch, account_id=acct)
            except Exception:
                _log.warning("aliases of evicted %s not removed", oid, exc_info=True)
            result.evicted.append(oid)

    try:
        if ch:
            update_kw = {"channel": ch, "account_id": acct, "peer": peer or ""}
            if peer_display is not None:
                update_kw["peer_display"] = peer_display
        else:
            update_kw = dict(_CLEARED)
        if db.get_session(session_id) is not None:
            db.update_session(session_id, **update_kw)
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            if conv is not None:
                conv["channel"] = ch
                conv["account_id"] = acct
                conv["peer"] = peer
    except Exception as e:
        result.error = f"persist failed: {type(e).__name__}: {e}"
        return result

    # Routing: this chat holds at most one channel conversation.
    try:
        _aliases.detach_session(session_id)
        if ch:
            peer_id = peer or _owner_peer(ch, acct)
            if peer_id:
                _aliases.attach(
                    channel=ch, account_id=acct,
                    peer_kind="direct", peer_id=peer_id,
                    agent_id=_agent_for(_s, session_id),
                    session_id=session_id,
                )
                result.peer = peer_id
    except Exception as e:
        result.error = f"routing failed: {type(e).__name__}: {e}"
        return result
    result.ok = True
    return result


def restore_channel_aliases(_s) -> int:
    """Give every chat already tied to a channel account by the chip, but
    missing its routing alias, that alias. Returns how many were written.

    Chats bound before picking wrote an alias recorded only the channel and
    account on the chat, so that account's messages kept going to the
    ``session_scope`` session. A chat counts as picked when its meta names
    a channel and account but no peer (a conversation that arrived from
    the channel records its peer and source). The most recently active such
    chat per account wins, and an account whose owner chat already has an
    alias is left alone.
    """
    from openprogram.runtime.agent_registry import session_aliases as _aliases
    from openprogram.runtime.context.session_db import default_db

    db = default_db()
    seen: set[tuple[str, str]] = set()
    written = 0
    for sess in db.list_sessions(limit=10**9):
        ch = sess.get("channel")
        acct = sess.get("account_id")
        if not ch or not acct or (ch, acct) in seen:
            continue
        if sess.get("peer_id") or sess.get("source") == ch:
            continue  # arrived from the channel: routed by its binding
        seen.add((ch, acct))
        peer_id = _owner_peer(ch, acct)
        if not peer_id:
            continue
        if _aliases.lookup(ch, acct, {"kind": "direct", "id": peer_id}) is not None:
            continue
        _aliases.attach(
            channel=ch, account_id=acct, peer_kind="direct", peer_id=peer_id,
            agent_id=sess.get("agent_id") or _s._default_agent_id(),
            session_id=sess["id"],
        )
        written += 1
    return written


async def handle_set_conversation_channel(ws, cmd: dict):
    """Bind (or unbind) a conversation to a chat channel + account."""
    from openprogram.interfaces.server import server as _s
    session_id = cmd.get("session_id")
    ch = (cmd.get("channel") or "").strip().lower() or None
    acct_id = (cmd.get("account_id") or "").strip() or None
    peer = (cmd.get("peer") or "").strip() or None
    peer_display = (cmd.get("peer_display") or "").strip() or None
    result = ChannelBindResult()
    if not session_id:
        result.error = "session_id required"
    else:
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
        if conv is None:
            result.error = f"unknown conversation {session_id!r}"
        elif ch is None and (acct_id or peer):
            result.error = "channel must be set when account_id / peer is set"
        else:
            # Writes this conversation (and any it takes the binding from)
            # under their locks: in its lane, off the loop.
            from openprogram.state.store.session.conversation_work import run_for_conversation
            result = await run_for_conversation(
                session_id, bind_conversation_channel,
                _s, session_id, ch, acct_id, peer, peer_display,
            )
            for oid in result.evicted:
                try:
                    await ws.send_text(json.dumps({
                        "type": "session_channel_updated",
                        "data": {
                            "session_id": oid,
                            "ok": True,
                            "channel": None,
                            "account_id": None,
                            "peer": None,
                            "evicted_by": session_id,
                        },
                    }, default=str))
                except Exception:
                    # Evicted session's peers see the change on their next
                    # load.
                    _log.debug(
                        "channel eviction notice not broadcast for %s", oid, exc_info=True)
    await ws.send_text(json.dumps({
        "type": "session_channel_updated",
        "data": {
            "session_id": session_id,
            "ok": result.ok,
            "channel": ch,
            "account_id": acct_id,
            "peer": peer,
            "error": result.error,
        },
    }, default=str))
