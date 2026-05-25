"""Inbound-message → agent-session dispatcher.

Each channel backend calls :func:`dispatch_inbound` for every incoming
external message. 该函数协调:

  1. 路由 ``(channel, account_id, peer)`` 到具体 agent (binding / alias)
  2. 算出 session_key (按 agent.session_scope + reset policy)
  3. 加载 / 创建 session (SessionDB)
  4. 跑 agent turn (process_user_turn)
  5. 可选 progress streaming: 实时编辑占位消息显示工具进度
  6. 把消息持久化 + 给 webui WS 推一份

子模块拆分:

  _session_store.py    session 路径、创建、加载、保存、默认标题
  _session_routing.py  session_key 计算 + reset policy
  _broadcast.py        webui WS push

本文件只承担 dispatch_inbound 主流程 + progress streaming state (跟
dispatch 流程紧绑, 不适合拆出去因为需要在 closure 里共享 state).
"""
from __future__ import annotations

import json
import sys
import time
from typing import Optional

from openprogram.agents import manager as _agents
from openprogram.channels import bindings as _bindings
from openprogram.channels._broadcast import (
    broadcast_channel_turn as _broadcast_channel_turn,
    poke_live_webui as _poke_live_webui,
)
from openprogram.channels._session_routing import (
    apply_reset_policy as _apply_reset_policy,
    session_key_for_agent as _session_key_for_agent,
)
from openprogram.channels._session_store import (
    load_or_init_session as _load_or_init_session,
)


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

    # ---- 路由: alias > binding -----------------------------------------
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

    # ---- session 创建 / 加载 -------------------------------------------
    meta, _ = _load_or_init_session(
        agent_id=agent_id,
        session_key=session_key,
        channel=channel,
        account_id=account_id,
        peer=peer,
        user_display=user_display or str(peer_id),
    )

    # ---- run config 加载 (permission/tools/effort) ---------------------
    from openprogram.agent.session_config import (
        load_session_run_config,
        permission_from_config,
        tools_override_from_config,
    )
    run_cfg = load_session_run_config(session_key)

    # ---- progress streaming state ---------------------------------------
    # 仅在 progress_stream=True 且占位发送成功后激活. progress_handle 为
    # None 时所有 streaming-edit 逻辑跳过, 保持旧行为.
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
                # 不 editable (WeChat 空字符串 sentinel) 或 _placeholder_mid
                # 是 None → 降级回非 streaming, 占位仍然发出去了但不参与
                # 后续 edit. WeChat 在这种降级下用户看到的是 "⏳..." 加上
                # 一条完整 reply, 不完美但不出错.
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

    # ---- dispatcher 调用 + stream event 监听 ----------------------------
    from openprogram.agent.dispatcher import (
        TurnRequest,
        process_user_turn,
    )

    captured_user_id: list[str] = []
    captured_assistant_id: list[str] = []

    def _on_event(env: dict) -> None:
        # 转发给 webui WS (existing behavior: 让 TUI 看见 streaming)
        try:
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
            # 把占位改成错误消息, adapter 不必再发. 用户看到的是单条
            # 带错误的消息, 没 placeholder 残留.
            _maybe_edit(err_text, force=True)
            return None
        return err_text

    reply_text = (result.final_text or "").strip() or "(empty reply)"
    user_msg_id = result.user_msg_id
    assistant_msg_id = result.assistant_msg_id

    # ---- 持久化 + webui WS push ----------------------------------------
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

    from openprogram.agent.session_db import default_db
    refreshed = default_db().get_session(session_key)
    if refreshed is not None:
        refreshed.setdefault("_last_touched", time.time())
        _poke_live_webui(agent_id, session_key, refreshed,
                         default_db().get_messages(session_key))

    # ---- Progress streaming: 把占位 edit 成完整 reply, 返回 None -------
    # reply 超长时占位放第一段, 余下用新消息追加.
    if progress_handle is not None:
        from openprogram.channels._transport import MAX_CHARS as _MAX_CHARS
        limit = _MAX_CHARS.get(channel, 1800)
        if len(reply_text) <= limit:
            _maybe_edit(reply_text, force=True)
        else:
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
