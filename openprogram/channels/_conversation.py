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
    progress_stream: bool = False,
) -> Optional[str]:
    """End-to-end inbound handling.

    ``progress_stream=False`` (default): 旧行为, 返回完整 assistant reply
    字符串供 adapter 自己发. 调用方拿到字符串后用 platform SDK / outbound
    发回去.

    ``progress_stream=True``: 进入 streaming 模式. dispatch 内部会:
       1. 先在目标 chat 发一条占位消息 "⏳ working...", 拿到 message_id
       2. 接 dispatcher emit 的 stream envelope, 按 tool 事件实时 edit 占位
          ("⚙ bash" → "✓ bash" → "⚙ read" → ...), 节流 1s 一次
       3. 最终用完整 reply edit 占位 (超长则占位放第一段 + 尾段发新消息)
       4. 返回 None 表示 adapter 不需要再发 reply
    任何 streaming 步骤失败 (占位发不出 / 平台不支持 edit / WeChat) 都会
    无声降级回非 streaming 行为, 返回 reply 字符串.

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

    # Progress-streaming state — 仅在 progress_stream=True 且占位发送成功
    # 后激活. progress_handle 为 None 时所有 streaming-edit 逻辑跳过, 保持
    # 旧行为.
    progress_handle = None
    progress_lines: list[str] = []
    last_edit_ts: list[float] = [0.0]

    if progress_stream:
        try:
            from openprogram.channels import _transport
            from openprogram.channels.base import MessageHandle as _MH
            _placeholder_mid = _transport.post_message(
                channel, account_id, str(peer_id), "⏳ working...",
            )
            if _placeholder_mid:
                _h = _MH(channel, account_id, str(peer_id), _placeholder_mid)
                if _h.editable:
                    progress_handle = _h
                # 不 editable (WeChat sentinel 空字符串) 或 _placeholder_mid
                # 是 None → 降级回非 streaming, 占位仍然发出去了但不参与
                # 后续 edit. WeChat 在这种降级下用户看到的是 "⏳ working..."
                # + 之后另一条完整 reply, 不完美但不出错.
        except Exception:
            progress_handle = None

    def _maybe_edit(text: str, *, force: bool = False) -> None:
        """节流的 progress edit. 至少 1 秒间隔, force=True 跳过节流."""
        if progress_handle is None:
            return
        now = time.time()
        if not force and now - last_edit_ts[0] < 1.0:
            return
        last_edit_ts[0] = now
        try:
            from openprogram.channels import _transport
            _transport.patch_message(
                progress_handle.platform, progress_handle.account_id,
                progress_handle.target, progress_handle.message_id, text,
            )
        except Exception:
            pass

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

        # Progress streaming: 按 tool 边界 edit 占位消息.
        if progress_handle is None:
            return
        data = env.get("data") or {}
        ev = data.get("event") or {}
        ev_type = ev.get("type")
        if ev_type == "tool_use":
            tool_name = ev.get("tool") or "?"
            progress_lines.append(f"⚙ {tool_name}")
            _maybe_edit("\n".join(progress_lines))
        elif ev_type == "tool_result":
            tool_name = ev.get("tool") or "?"
            is_err = bool(ev.get("is_error"))
            marker = "✗" if is_err else "✓"
            # 把最近一个 "⚙ {tool_name}" 改成 "✓/✗ {tool_name}"
            for i in range(len(progress_lines) - 1, -1, -1):
                if progress_lines[i] == f"⚙ {tool_name}":
                    progress_lines[i] = f"{marker} {tool_name}"
                    break
            _maybe_edit("\n".join(progress_lines))

    req = TurnRequest(
        session_id=session_key,
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
        err_text = f"[error] {type(e).__name__}: {e}"
        if progress_handle is not None:
            # 把占位改成错误消息, adapter 不必再发. 用户看到的是单条带
            # 错误的消息, 不会有 placeholder 残留.
            _maybe_edit(err_text, force=True)
            return None
        return err_text

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

    # Progress streaming: 把占位消息 edit 成完整 reply, 然后返回 None
    # 让 adapter 知道 reply 已经送达. reply 超长时占位放第一段, 余下用
    # 新消息追加.
    if progress_handle is not None:
        from openprogram.channels._transport import MAX_CHARS as _MAX_CHARS
        limit = _MAX_CHARS.get(channel, 1800)
        if len(reply_text) <= limit:
            _maybe_edit(reply_text, force=True)
        else:
            # 占位放前 (limit - 30) 字符 + 提示, 后段单独发新消息.
            head = reply_text[: limit - 30]
            tail = reply_text[limit - 30 :]
            _maybe_edit(head + "\n... (continued ↓)", force=True)
            try:
                from openprogram.channels import _transport
                _transport.post_message(
                    channel, account_id, str(peer_id), tail,
                )
            except Exception:
                pass
        return None

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
    and appends both messages to its transcript when the session_id matches
    the currently-viewed session — so a wechat user typing "hello"
    shows up live in an attached `openprogram` TUI without a /resume
    refresh. session_key is also the session_id the TUI uses (same
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
                "session_id": session_key,
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
