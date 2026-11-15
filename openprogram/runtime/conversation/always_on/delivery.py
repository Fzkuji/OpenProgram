"""Send always-on reports to channel conversations: the ones an Agent picked
and the one the chat is bound to.

A report is saved first, then sent in the background, one message per
target, through the channels' stateless outbound path
(``openprogram.runtime.channel_link``). A failed send
never loses the report or fails the tool; its result is recorded on the
report row. Delivery is at least once: a restart resends what has no result.
A target that already has a conversation of its own also gets the report
there as a message, so a reply on the phone has its context.

Design: docs/reference/design/runtime/always-on-agents.html
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from openprogram.runtime.conversation.always_on import store

_log = logging.getLogger(__name__)

# Waits before the second and third try when the connection could not be made.
RETRY_DELAYS_S = (60.0, 600.0)


def _key(target: dict[str, Any]) -> tuple[str, str, str]:
    return (str(target.get("channel") or ""), str(target.get("account_id") or ""),
            str(target.get("peer_id") or ""))


def channel_targets(agent_id: str) -> list[dict[str, str]]:
    """Channel conversations reports can go to: peer bindings of this Agent
    and the channel conversations it already has, newest first."""
    from openprogram.runtime.agent_registry.manager import ALWAYS_ON_CHANNELS
    from openprogram.runtime import channel_link
    from openprogram.runtime.context.session_db import default_db

    found: dict[tuple[str, str, str], dict[str, str]] = {}

    def add(channel: str, account_id: str, peer_id: str, label: str) -> None:
        target = {"channel": channel, "account_id": account_id, "peer_id": peer_id,
                  "label": label or peer_id}
        if channel in ALWAYS_ON_CHANNELS and account_id and peer_id:
            found.setdefault(_key(target), target)

    try:
        rows = default_db().list_sessions(agent_id=agent_id, limit=500, include_archived=True)
    except Exception:
        rows = []
    for row in rows:
        if row.get("channel") and row.get("peer_id"):
            add(str(row["channel"]), str(row.get("account_id") or ""), str(row["peer_id"]),
                str(row.get("peer_display") or ""))
    for binding in channel_link.peer_bindings(agent_id):
        match = binding.get("match") or {}
        peer = match.get("peer") or {}
        if not peer.get("id"):
            continue
        channel = str(match.get("channel") or "")
        account_ids = ([match["account_id"]] if match.get("account_id")
                       else channel_link.account_ids(channel))
        for account_id in account_ids:
            add(channel, str(account_id), str(peer["id"]), "")
    return list(found.values())


def report_text(agent_name: str, report: dict[str, Any]) -> str:
    """Markdown; the channel transport renders it for each platform."""
    return report_markdown(report, heading=f"{agent_name} · {report.get('title') or ''}")


def report_markdown(report: dict[str, Any], *, heading: str | None = None) -> str:
    """The bold title (or ``heading``), the body, then the links as a list."""
    parts = [f"**{heading if heading is not None else report.get('title') or ''}**"]
    if report.get("body"):
        parts.append(str(report["body"]))
    if report.get("links"):
        parts.append("\n".join(f"- {link}" for link in report["links"]))
    return "\n\n".join(parts)


def without_repeats(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each conversation once; the first row for it stays."""
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for target in targets:
        found.setdefault(_key(target), target)
    return list(found.values())


def _readable(address_part: str) -> str:
    """Percent-escapes decoded for reading, unless that would not read safely."""
    try:
        decoded = unquote(address_part, errors="strict")
    except ValueError:
        return address_part
    return decoded if decoded.isprintable() and not re.search(r"[<>]", decoded) else address_part


def _link_names(link: str) -> tuple[str, str, str] | None:
    """A web address's names, shortest first: its host, the host with the last
    path segment, and the whole address without scheme or sign-in. None for
    anything else. A backslash is refused: browsers read it as a slash and
    ``urlsplit`` does not, so the host named here could differ from the one opened."""
    if re.search(r"[\s<>\\]", link):
        return None
    try:
        parts = urlsplit(link)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    path = parts.path.rstrip("/")
    tail = path.rpartition("/")[2]
    whole = (f"{host}:{port}" if port else host) + path
    whole += (f"?{parts.query}" if parts.query else "") + (f"#{parts.fragment}" if parts.fragment else "")
    return host, f"{host}/{_readable(tail)}" if tail else host, _readable(whole)


def _labelled_links(links: list[str]) -> list[str]:
    """Web addresses as ``[name](url)``: the host, or a longer name where a
    shorter one would read the same for two different links. Anything else
    stays as it is."""
    named = [_link_names(link) for link in links]
    hosts = Counter(names[0] for names in named if names)
    short = [names and (names[0] if hosts[names[0]] == 1 else names[1]) for names in named]
    repeated = Counter(short)
    labelled = []
    for link, names, name in zip(links, named, short):
        if names is None:
            labelled.append(link)
            continue
        label = re.sub(r"([\\\[\]*_`~$&])", r"\\\1", names[2] if repeated[name] > 1 else name)
        labelled.append(f"[{label}]({link.replace('(', '%28').replace(')', '%29')})")
    return labelled


def report_message(report: dict[str, Any]) -> str:
    """What a report says in the conversation: its body and links, the way the
    Agent would write them. The title is for lists and notifications. A report
    with only a title has nothing else to show."""
    if not store.shows_anything(report.get("body")):
        return report_markdown(report)
    parts = [str(report["body"])]
    if report.get("links"):
        parts.append("\n".join(f"- {link}" for link in _labelled_links(report["links"])))
    return "\n\n".join(parts)


def pending(targets: list[dict[str, Any]], *, mirror_from: str | None = None,
            agent_id: str | None = None) -> list[dict[str, Any]]:
    """Delivery rows for ``targets``. With ``mirror_from`` (the reporting
    conversation), a row names the target's channel conversation, if it has
    one, in ``session_id``: a reply there has to know what it answers. Not
    finding it costs the copy only, never the report."""
    rows = [{**target, "status": "sending"} for target in targets]
    if mirror_from is not None:
        for row in rows:
            try:
                found = channel_conversation(row, str(agent_id or ""))
            except Exception as exc:  # noqa: BLE001 - the report is saved and sent regardless
                _log.warning("channel conversation of %s not read: %s: %s",
                             _key(row), type(exc).__name__, exc)
                continue
            if found and found != mirror_from:
                row["session_id"] = found
    return rows


def channel_conversation(target: dict[str, Any], agent_id: str) -> str | None:
    """The conversation a message from this channel peer lands in, or None
    when there is none yet. An alias decides it, as in inbound routing, even
    when its conversation is archived: the reply goes there, not elsewhere."""
    from openprogram.runtime.agent_registry import session_aliases
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on.runtime import owner_conversation

    channel, account_id, peer_id = _key(target)
    db = default_db()
    aliases = [row for row in session_aliases.list_all()
               if row.get("channel") == channel and row.get("account_id") == account_id]
    named = [row for row in aliases if str((row.get("peer") or {}).get("id")) == peer_id]
    catch_all = [row for row in aliases if (row.get("peer") or {}).get("id") == "*"]
    if named or catch_all:
        session = db.get_session(str((named or catch_all)[-1].get("session_id") or ""))
        return str(session["id"]) if owner_conversation(session) else None
    for session in db.list_sessions(agent_id=agent_id, limit=500):
        if (session.get("channel") == channel and str(session.get("account_id") or "") == account_id
                and str(session.get("peer_id") or "") == peer_id and owner_conversation(session)):
            return str(session["id"])
    return None


def mirror_sessions(report: dict[str, Any]) -> list[str]:
    """Other conversations holding a copy of ``report``."""
    return list(dict.fromkeys(str(row["session_id"]) for row in report.get("deliveries") or []
                              if row.get("session_id")))


def _send_once(target: dict[str, Any], text: str, files: list[str],
               prepared: dict[tuple[str, str, str], str]) -> tuple[bool, bool, str, str]:
    """(sent, connection never made, error kind, detail).

    ``prepared`` keeps each target's message between tries, so a try that
    could not connect does not upload the report's files a second time.
    """
    from openprogram.runtime import channel_link
    from openprogram.state.attachments import find_markers

    try:
        key = _key(target)
        if key not in prepared:
            # A marker is for the App; a chat gets the file, or a line saying why not.
            prepared[key] = channel_link.flatten_tables(
                channel_link.upload_marked_files(target["channel"], target["account_id"], target["peer_id"],
                                                 text, files) if find_markers(text) else text)
        sent = channel_link.send_message(target["channel"], target["account_id"],
                                         target["peer_id"], prepared[key])
        if sent.ok:
            return True, False, "", ""
        return False, sent.connect_failed, sent.error_kind or "unknown", str(sent.error_detail or "")[:300]
    except Exception as exc:  # noqa: BLE001 - a send must never lose the report
        return False, False, "unknown", f"{type(exc).__name__}: {exc}"[:300]


def deliver(session_id: str, agent_name: str, report: dict[str, Any],
            on_done: Callable[[dict[str, Any] | None], None] | None = None) -> threading.Thread | None:
    """Send ``report`` to its pending deliveries in a daemon thread.

    A target whose connection could not be made stays ``sending`` and is
    tried again after each of ``RETRY_DELAYS_S``; every other outcome is
    final. Each pass is saved, so a restart resends only what is unresolved.
    """
    rows = [dict(row) for row in report.get("deliveries") or []]
    if not any(row.get("status") == "sending" for row in rows):
        return None

    def run() -> None:
        text = report_text(agent_name, report)
        files = report.get("attached_files") or []
        prepared: dict[tuple[str, str, str], str] = {}
        retries_left = len(RETRY_DELAYS_S)
        for delay in (0.0, *RETRY_DELAYS_S):
            waiting = [row for row in rows if row.get("status") == "sending"]
            if not waiting:
                return
            time.sleep(delay)
            for row in waiting:
                ok, connect_failed, error, detail = _send_once(row, text, files, prepared)
                if connect_failed and retries_left:
                    continue
                row["status"] = "sent" if ok else "failed"
                if not ok:
                    row["error"], row["detail"] = error, detail
            retries_left -= 1
            updated = store.update_report(session_id, report["id"], deliveries=[dict(row) for row in rows])
            if on_done is not None:
                on_done(updated)

    thread = threading.Thread(target=run, name=f"always-on-deliver-{session_id}", daemon=True)
    thread.start()
    return thread


def close_cut_off(session_id: str, report: dict[str, Any]) -> dict[str, Any] | None:
    """Mark a report's unfinished deliveries as failed: too old to resend."""
    rows = [{**row, "status": "failed", "error": "interrupted", "detail": "cut off by a restart"}
            if row.get("status") == "sending" else row
            for row in report.get("deliveries") or []]
    return store.update_report(session_id, report["id"], deliveries=rows)
