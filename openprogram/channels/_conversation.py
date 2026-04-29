"""Inbound-message → agent-session dispatcher.

Each channel backend calls :func:`dispatch_inbound` for every incoming
external message. This module does all the bookkeeping:

  1. Route ``(channel, account_id, peer)`` to an agent via bindings.
  2. Resolve / create the agent's session for that peer.
  3. Load the session's history and render it as a text prefix.
  4. Run the turn through the agent's runtime.
  5. Append user + assistant messages to the session file.
  6. Push a live update to any connected Web UI tabs.

Sessions live under ``<state>/agents/<agent_id>/sessions/<session_key>/``.
``session_key`` is ``{account_id}_{peer_kind}_{peer_id}`` sanitized for
disk — uniquely identifies a thread within one agent.

The persistence file format matches what the Web UI reads for its own
conversations (meta.json + messages.json), so bound sessions appear in
the sidebar alongside anything the user started locally.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from openprogram.agents import manager as _agents
from openprogram.agents import runtime_registry as _runtimes
from openprogram.channels import bindings as _bindings


MAX_HISTORY_CHARS = 60_000


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def dispatch_inbound(
    *,
    channel: str,
    account_id: str,
    peer_kind: str,
    peer_id: str,
    user_text: str,
    user_display: str = "",
) -> str:
    """End-to-end inbound handling. Returns the assistant reply string
    so the channel backend can forward it to the external user.

    Never raises into the channel's poll loop — any failure (no
    provider configured, runtime crash, etc.) is flattened into an
    error-shaped reply string that the bot can surface to the user
    rather than silently dropping the message.
    """
    peer = {"kind": peer_kind or "direct", "id": str(peer_id)}

    # Session alias: user said "route this peer into session X".
    # Highest priority — bypasses both binding-based agent selection
    # and scope-based session key computation.
    from openprogram.agents import session_aliases as _aliases
    alias = _aliases.lookup(channel, account_id, peer)
    if alias is not None:
        agent_id, session_key = alias
        agent = _agents.get(agent_id)
        if agent is None:
            return (f"[unknown agent {agent_id!r}] — alias points at a "
                    f"deleted agent.")
    else:
        try:
            agent_id = _bindings.route(channel, account_id, peer)
        except Exception as e:  # noqa: BLE001
            return f"[routing error] {type(e).__name__}: {e}"
        if not agent_id:
            return ("[no agent configured] Run `openprogram agents add "
                    "main` and configure a provider.")

        agent = _agents.get(agent_id)
        if agent is None:
            return (f"[unknown agent {agent_id!r}] — binding points at a "
                    f"deleted agent.")

        base_key = _session_key_for_agent(
            agent, channel, account_id, peer,
        )
        session_key = _apply_reset_policy(agent, base_key)
    # Make sure SessionDB has a row for this session_key with the
    # full peer/account metadata before dispatcher takes over (its
    # default create only sets a subset of fields).
    meta, _ = _load_or_init_session(
        agent_id=agent_id,
        session_key=session_key,
        channel=channel,
        account_id=account_id,
        peer=peer,
        user_display=user_display or str(peer_id),
    )
    from openprogram.agent.session_config import (
        load_session_run_config,
        permission_from_config,
        tools_override_from_config,
    )
    run_cfg = load_session_run_config(session_key)

    # Hand the rest of the turn — agent run, message append, FTS
    # indexing — to the unified dispatcher. Channel turns reuse the
    # bound session's run settings; source filtering still hides tools
    # marked unsafe for this transport.
    from openprogram.agent.dispatcher import (
        TurnRequest,
        process_user_turn,
    )

    captured_user_id: list[str] = []
    captured_assistant_id: list[str] = []

    def _on_event(env: dict) -> None:
        # Forward streaming events to any connected webui clients so
        # an attached TUI sees the channel reply in real time.
        try:
            import sys
            srv = sys.modules.get("openprogram.webui.server")
            if srv is not None:
                srv._broadcast(json.dumps(env, default=str))
        except Exception:
            pass
        if env.get("type") == "chat_ack":
            data = env.get("data") or {}
            if data.get("msg_id"):
                captured_user_id.append(str(data["msg_id"]))

    req = TurnRequest(
        conv_id=session_key,
        user_text=user_text,
        agent_id=agent_id,
        source=channel,
        peer_display=user_display or str(peer_id),
        peer_id=str(peer_id),
        permission_mode=permission_from_config(run_cfg, default="auto"),
        tools_override=tools_override_from_config(run_cfg),
        thinking_effort=run_cfg.thinking_effort,
    )
    try:
        result = process_user_turn(req, on_event=_on_event)
    except Exception as e:  # noqa: BLE001
        return f"[error] {type(e).__name__}: {e}"

    reply_text = (result.final_text or "").strip() or "(empty reply)"
    user_msg_id = result.user_msg_id
    assistant_msg_id = result.assistant_msg_id

    # Build user/reply message dicts for the channel_turn broadcast —
    # the TUI consumer (cli_ink) renders both on receipt without
    # needing a /resume refresh.
    user_msg = {
        "role": "user",
        "id": user_msg_id,
        "content": user_text,
        "timestamp": time.time(),
        "source": channel,
        "peer_display": user_display or str(peer_id),
        "peer_id": str(peer_id),
    }
    reply_msg = {
        "role": "assistant",
        "id": assistant_msg_id,
        "content": reply_text,
        "timestamp": time.time(),
        "source": channel,
    }
    _broadcast_channel_turn(agent_id, session_key, user_msg, reply_msg)

    # Refresh the meta dict from the just-updated DB row and broadcast
    # the per-session "updated" envelope so any open webui sidebars
    # bump this conversation to the top.
    from openprogram.agent.session_db import default_db
    refreshed = default_db().get_session(session_key)
    if refreshed is not None:
        refreshed.setdefault("_last_touched", time.time())
        _poke_live_webui(agent_id, session_key, refreshed,
                         default_db().get_messages(session_key))
    return reply_text


# ---------------------------------------------------------------------------
# Session storage — file layout compatible with webui.persistence
# ---------------------------------------------------------------------------

def _session_path(agent_id: str, session_key: str) -> Path:
    return _agents.sessions_dir(agent_id) / session_key


def _meta_path(agent_id: str, session_key: str) -> Path:
    return _session_path(agent_id, session_key) / "meta.json"


def _messages_path(agent_id: str, session_key: str) -> Path:
    return _session_path(agent_id, session_key) / "messages.json"


def _load_or_init_session(
    *,
    agent_id: str,
    session_key: str,
    channel: str,
    account_id: str,
    peer: dict[str, Any],
    user_display: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load (or create) the SQLite-backed session row + replay its
    message log. Channels used to write meta.json + messages.json
    files; SessionDB now owns both. We still mkdir the legacy folder
    so any sub-paths (e.g. `trees/` for webui context-tree dumps)
    keep working without churn."""
    from openprogram.agent.session_db import default_db
    db = default_db()

    _session_path(agent_id, session_key).mkdir(parents=True, exist_ok=True)

    sess = db.get_session(session_key)
    if sess is None:
        meta: dict[str, Any] = {
            "id": session_key,
            "agent_id": agent_id,
            "title": _default_title(channel, user_display),
            "created_at": time.time(),
            "channel": channel,
            "source": channel,
            "account_id": account_id,
            "peer": dict(peer),
            "peer_kind": peer.get("kind"),
            "peer_id": peer.get("id"),
            "peer_display": user_display,
            "_titled": True,
        }
        db.create_session(
            session_key, agent_id,
            title=meta["title"],
            created_at=meta["created_at"],
            channel=channel,
            source=channel,
            account_id=account_id,
            peer_kind=peer.get("kind"),
            peer_id=peer.get("id"),
            peer_display=user_display,
            peer=dict(peer),  # full peer dict goes to extra_meta
            _titled=True,
        )
        return meta, []

    meta = dict(sess)
    # Refresh peer display if the upstream handle changed.
    if user_display and meta.get("peer_display") != user_display:
        meta["peer_display"] = user_display
        meta["title"] = _default_title(channel, user_display)
        db.update_session(
            session_key,
            peer_display=user_display,
            title=meta["title"],
        )
    # Backfill peer dict from columns when missing from extra_meta
    # (older rows).
    if "peer" not in meta and meta.get("peer_id"):
        meta["peer"] = {"kind": meta.get("peer_kind") or "direct",
                        "id": meta["peer_id"]}
    messages = db.get_messages(session_key)
    return meta, messages


def _save_session(agent_id: str, session_key: str,
                  meta: dict[str, Any],
                  messages: list[dict[str, Any]],
                  *, new_messages: list[dict[str, Any]] | None = None) -> None:
    """Persist meta updates and append any new messages.

    `new_messages` lets the caller skip re-writing the entire history
    on every turn (the old JSON-file path had no choice). Pass the
    just-ingested rows; if omitted we fall back to inferring "what's
    new" by id-diff against the DB, which is slower but still correct."""
    from openprogram.agent.session_db import default_db
    db = default_db()

    # Always touch the legacy dir so other code that drops sub-paths
    # there (webui's `trees/`) stays happy.
    _session_path(agent_id, session_key).mkdir(parents=True, exist_ok=True)

    db.update_session(
        session_key,
        agent_id=agent_id,
        title=meta.get("title"),
        head_id=meta.get("head_id"),
        peer_display=meta.get("peer_display"),
        provider_name=meta.get("provider_name"),
        model=meta.get("model"),
    )
    if new_messages is None:
        existing_ids = {m["id"] for m in db.get_messages(session_key)}
        new_messages = [m for m in messages if m.get("id") not in existing_ids]
    if new_messages:
        db.append_messages(session_key, new_messages)


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)


def _session_key_for_agent(agent, channel: str, account_id: str,
                           peer: dict[str, Any]) -> str:
    """Compute the session-routing key according to the agent's
    ``session_scope``. OpenClaw's dmScope values:

      main                      — one shared session for all DMs
      per-peer                  — one per sender, across channels
      per-channel-peer          — one per (channel, sender)
      per-account-channel-peer  — one per (account, channel, sender)
                                  — our previous default

    Group / channel peers always isolate by peer id regardless of
    scope, since a shared session across different groups is never
    what anyone wants.
    """
    kind = str(peer.get("kind") or "direct")
    pid = str(peer.get("id") or "")
    scope = getattr(agent, "session_scope", None) or "per-account-channel-peer"

    if kind in ("group", "channel"):
        raw = f"{channel}_{account_id}_{kind}_{pid}"
    elif scope == "main":
        raw = "main"
    elif scope == "per-peer":
        raw = f"peer_{pid}"
    elif scope == "per-channel-peer":
        raw = f"{channel}_{kind}_{pid}"
    else:  # per-account-channel-peer (default)
        raw = f"{account_id}_{kind}_{pid}"

    safe = re.sub(r"[^A-Za-z0-9_-]", "-", raw).strip("-")
    return safe or "unknown"


def _apply_reset_policy(agent, base_key: str) -> str:
    """Honor the agent's daily / idle session reset settings.

    Daily reset: if ``agent.session_daily_reset`` is ``HH:MM``, we
    suffix the key with the current reset-window's date — rolling
    over at that hour starts a brand-new session automatically.

    Idle reset: if ``agent.session_idle_minutes > 0``, check the
    existing session's ``_last_touched`` against wall clock; if we're
    past the threshold, suffix the key with an epoch minute so the
    next turn creates a fresh file on disk.

    Reset suffixes are transparent to the UI — previous sessions
    stay on disk (readable via the sidebar) and the new one picks up
    from scratch.
    """
    import datetime as _dt

    key = base_key
    daily = (getattr(agent, "session_daily_reset", "") or "").strip()
    if daily:
        try:
            h, m = daily.split(":", 1)
            reset_h, reset_m = int(h), int(m)
            now = _dt.datetime.now()
            window_start = now.replace(
                hour=reset_h, minute=reset_m, second=0, microsecond=0,
            )
            if now < window_start:
                window_start -= _dt.timedelta(days=1)
            key += f"_{window_start.strftime('%Y%m%d')}"
        except (ValueError, AttributeError):
            pass

    idle_min = int(getattr(agent, "session_idle_minutes", 0) or 0)
    if idle_min > 0:
        # Check the previous session (base + any daily suffix). If
        # it's stale, add an idle suffix so we rotate.
        prev_meta_path = _meta_path(agent.id, key)
        if prev_meta_path.exists():
            try:
                import json as _json
                prev = _json.loads(prev_meta_path.read_text(encoding="utf-8"))
                last = float(prev.get("_last_touched") or 0)
                if last and (time.time() - last) > idle_min * 60:
                    key += f"_cut{int(time.time() // 60)}"
            except Exception:
                pass

    return key


def _default_title(channel: str, user_display: str) -> str:
    pretty = {
        "wechat": "WeChat",
        "telegram": "Telegram",
        "discord": "Discord",
        "slack": "Slack",
    }.get(channel, channel)
    return f"{pretty}: {user_display}"


# ---------------------------------------------------------------------------
# Live Web UI push (best-effort)
# ---------------------------------------------------------------------------

def _broadcast_channel_turn(agent_id: str, session_key: str,
                            user_msg: dict[str, Any],
                            reply_msg: dict[str, Any]) -> None:
    """Push the just-completed channel turn (user message + assistant
    reply) to every connected WS client. The TUI watches for this event
    and appends both messages to its transcript when the conv_id matches
    the currently-viewed session — so a wechat user typing "hello"
    shows up live in an attached `openprogram` TUI without a /resume
    refresh. session_key is also the conv_id the TUI uses (same
    `default_direct_<peer>` layout), no translation needed.
    """
    try:
        import sys
        srv = sys.modules.get("openprogram.webui.server")
        if srv is None:
            return
        payload = {
            "type": "channel_turn",
            "data": {
                "conv_id": session_key,
                "agent_id": agent_id,
                "user": {
                    "id": user_msg.get("id"),
                    "text": user_msg.get("content"),
                    "peer_display": user_msg.get("peer_display"),
                    "source": user_msg.get("source"),
                },
                "assistant": {
                    "id": reply_msg.get("id"),
                    "text": reply_msg.get("content"),
                    "source": reply_msg.get("source"),
                },
            },
        }
        srv._broadcast(json.dumps(payload, default=str))
    except Exception:
        pass


def _poke_live_webui(agent_id: str, session_key: str,
                     meta: dict[str, Any],
                     messages: list[dict[str, Any]]) -> None:
    """Tell any connected WebSocket clients a channel session changed.

    Only does anything if ``openprogram.webui.server`` is loaded in
    this process (true for the Web UI server path, possibly true for
    the worker). Failures silently swallow — persistence already
    happened; live push is a nicety.
    """
    try:
        import sys
        srv = sys.modules.get("openprogram.webui.server")
        if srv is None:
            return
        # Broadcast a minimal "channel session updated" envelope that
        # clients currently viewing that agent can use to refresh.
        payload = {
            "type": "agent_session_updated",
            "data": {
                "agent_id": agent_id,
                "session_id": session_key,
                "title": meta.get("title"),
                "head_id": meta.get("head_id"),
                "updated_at": meta.get("_last_touched"),
                "source": meta.get("channel"),
            },
        }
        srv._broadcast(json.dumps(payload, default=str))
    except Exception:
        pass
