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
    try:
        agent_id = _bindings.route(channel, account_id, peer)
    except Exception as e:  # noqa: BLE001
        return f"[routing error] {type(e).__name__}: {e}"
    if not agent_id:
        return ("[no agent configured] Run `openprogram agents add main` "
                "and configure a provider.")

    agent = _agents.get(agent_id)
    if agent is None:
        return f"[unknown agent {agent_id!r}] — binding points at a deleted agent."

    base_key = _session_key_for_agent(
        agent, channel, account_id, peer,
    )
    session_key = _apply_reset_policy(agent, base_key)
    meta, messages = _load_or_init_session(
        agent_id=agent_id,
        session_key=session_key,
        channel=channel,
        account_id=account_id,
        peer=peer,
        user_display=user_display or str(peer_id),
    )

    from openprogram.agents.context_engine import default_engine as _engine

    user_msg_id = uuid.uuid4().hex[:12]
    user_msg = {
        "role": "user",
        "id": user_msg_id,
        "parent_id": messages[-1]["id"] if messages else None,
        "content": user_text,
        "timestamp": time.time(),
        "source": channel,
    }
    _engine.ingest(messages, user_msg)

    # Assemble the prompt through the engine: it owns budget, history
    # rendering, and system-prompt composition. We take the user's
    # fresh turn out of the engine's "history" slice so the ingested
    # copy isn't double-rendered.
    assembled = _engine.assemble(agent, meta, messages[:-1])
    exec_content: list[dict] = []
    if assembled.system_prompt_addition:
        exec_content.append({
            "type": "text", "text": assembled.system_prompt_addition,
        })
    exec_content.extend(assembled.messages)
    exec_content.append({"type": "text", "text": user_text})

    try:
        rt = _runtimes.get_runtime_for(agent)
        reply = rt.exec(content=exec_content)
        reply_text = str(reply or "").strip() or "(empty reply)"
    except Exception as e:  # noqa: BLE001
        reply_text = f"[error] {type(e).__name__}: {e}"

    reply_msg = {
        "role": "assistant",
        "id": user_msg_id + "_reply",
        "parent_id": user_msg_id,
        "content": reply_text,
        "timestamp": time.time(),
        "source": channel,
    }
    _engine.ingest(messages, reply_msg)
    _engine.after_turn(agent, meta, messages)

    meta["head_id"] = messages[-1]["id"]
    meta["_last_touched"] = time.time()
    _save_session(agent_id, session_key, meta, messages)
    _poke_live_webui(agent_id, session_key, meta, messages)
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
    folder = _session_path(agent_id, session_key)
    folder.mkdir(parents=True, exist_ok=True)
    meta_p = _meta_path(agent_id, session_key)
    msgs_p = _messages_path(agent_id, session_key)

    meta: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    if msgs_p.exists():
        try:
            messages = json.loads(msgs_p.read_text(encoding="utf-8")) or []
        except (OSError, json.JSONDecodeError):
            messages = []

    if not meta:
        meta = {
            "id": session_key,
            "agent_id": agent_id,
            "title": _default_title(channel, user_display),
            "created_at": time.time(),
            "channel": channel,
            "account_id": account_id,
            "peer": dict(peer),
            "peer_display": user_display,
            "_titled": True,
        }
    else:
        # Keep display fresh in case the user's handle changed.
        if user_display and meta.get("peer_display") != user_display:
            meta["peer_display"] = user_display
            meta["title"] = _default_title(channel, user_display)

    return meta, messages


def _save_session(agent_id: str, session_key: str,
                  meta: dict[str, Any],
                  messages: list[dict[str, Any]]) -> None:
    folder = _session_path(agent_id, session_key)
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_meta_path(agent_id, session_key), meta)
    _atomic_write_json(_messages_path(agent_id, session_key), messages)


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
