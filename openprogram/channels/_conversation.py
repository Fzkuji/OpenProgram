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

import asyncio
import threading
import time
from typing import Optional

from openprogram.events import emit_ws_frame as _emit_ws_frame
from openprogram.agent.management import manager as _agents
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
# Per-session 串行化
# ---------------------------------------------------------------------------

# 同 session 串行队列: session_key → Lock. dispatcher 对同一 session 的并发
# turn 没有锁, 两条消息同时到会交错写库. 锁在 turn 全程持有, 同 session 的
# 下一条消息排队, 不同 session 并行.
# ponytail: 锁表随 session_key 单调增长不回收; 每 key 一个 Lock 很小, 真成
# 问题再换带回收的表.
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock_for(session_key: str) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_key)
        if lock is None:
            lock = _SESSION_LOCKS[session_key] = threading.Lock()
        return lock


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
    speaker_id: str | None = None,
    speaker_display: str | None = None,
    progress_stream: bool = False,
    attachments: Optional[list[dict]] = None,
) -> Optional[str]:
    """End-to-end inbound handling.

    ``attachments`` — TurnRequest.attachments 形状的 image block 列表
    (base 已下载落盘并转好 base64), 直接透传给 dispatcher; None = 无.

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

    # 事件层 tap：外部消息进来了（B 类，不属于任何 turn）。懒 import 防循环。
    try:
        from openprogram.events import emit_safe
        emit_safe("channel.message_inbound", "user",
                  {"channel": channel, "peer_kind": peer["kind"],
                   "chars": len(user_text or "")})
    except Exception:
        pass

    # ---- 路由: alias > binding -----------------------------------------
    from openprogram.agent.management import session_aliases as _aliases
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

    # ---- /answer · /decline 文本命令拦截 -------------------------------
    # 函数 runtime.ask 在本 channel 会话里挂了个待答问题，用户用一条
    # /answer <qid> <choice> 文本消息回答（聊天软件没有网页那种问题卡片）。
    # 命中且该 qid 属于本 session 才 resolve 并回执，不走 agent dispatch；
    # 否则（不是命令 / qid 不属于本 session）返回 None，照常进 agent。
    from openprogram.channels._question_commands import try_handle_question_command
    _receipt = try_handle_question_command(user_text, session_key)
    if _receipt is not None:
        return _receipt

    # ---- 同 session 串行 ------------------------------------------------
    # /answer·/decline 必须在锁外处理 (上面已 return): turn 停在
    # runtime.ask 时持锁等待, /answer 若也抢锁就自死锁.
    with _session_lock_for(session_key):
        return _run_session_turn(
            channel=channel, account_id=account_id, peer=peer,
            peer_id=peer_id, user_text=user_text,
            user_display=user_display, speaker_id=speaker_id,
            speaker_display=speaker_display, progress_stream=progress_stream,
            agent_id=agent_id, session_key=session_key,
            attachments=attachments,
        )


def _run_session_turn(
    *,
    channel: str,
    account_id: str,
    peer: dict,
    peer_id: str,
    user_text: str,
    user_display: str,
    speaker_id: str | None,
    speaker_display: str | None,
    progress_stream: bool,
    agent_id: str,
    session_key: str,
    attachments: Optional[list[dict]] = None,
) -> Optional[str]:
    """路由已完成、session 锁已持有 — 跑一个完整 turn: load session →
    agent turn → 持久化 → broadcast. 返回值语义同 dispatch_inbound."""
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
        project_defaults,
    )
    from openprogram.programs.permission_rule import load_merged_rules as _load_merged_rules
    run_cfg = load_session_run_config(session_key)
    _pdef = project_defaults(session_key)

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
            _result = _transport.post_message(
                channel, account_id, str(peer_id), "⏳ working...",
            )
            if _result.ok and _result.message_id:
                _h = _MH(channel, account_id, str(peer_id), _result.message_id)
                if _h.editable:
                    progress_handle = _h
                # 不 editable (WeChat 空 message_id) → 降级回非 streaming,
                # 占位仍然发出去了但不参与后续 edit. WeChat 在这种降级下
                # 用户看到的是 "⏳..." 加上一条完整 reply, 不完美但不
                # 出错.
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

    # ---- canonical Agent admission + stream event 监听 ------------------
    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.production_driver import CanonicalAgentAdapter

    captured_user_id: list[str] = []
    captured_assistant_id: list[str] = []

    def _on_event(env: dict) -> None:
        # 转发给 webui WS — 走事件总线 (ws.frame 事件), webui 订阅后原样
        # 广播. turn 级 channel_turn / agent_session_updated 已走同一机制
        # (_broadcast.py); 这里把流式帧也从"摸 webui 私有 _broadcast"改过来.
        _emit_ws_frame(env)
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

    from openprogram.agent.authority import paired_channel_authority
    _authority = paired_channel_authority(
        channel, account_id, speaker_id or "",
        speaker_display or user_display,
    )
    req = TurnRequest(
        session_id=session_key,
        user_text=user_text,
        agent_id=agent_id,
        source=channel,
        peer_display=user_display or str(peer_id),
        peer_id=str(peer_id),
        permission_mode=permission_from_config(
            run_cfg, default=_pdef.get("permission_mode") or "ask"),
        permission_rules=_load_merged_rules(session_key),
        additional_working_dirs=run_cfg.additional_working_dirs,
        tools_override=tools_override_from_config(run_cfg),
        thinking_effort=run_cfg.thinking_effort or _pdef.get("thinking_effort"),
        attachments=attachments or None,
        **_authority,
    )
    adapter = CanonicalAgentAdapter(event_sink=_on_event)
    try:
        admission = adapter.admit(
            req,
            trusted_actor=_authority,
            user_message_id=req.user_msg_id,
            config_snapshot_ref=f"channel:{channel}",
        )
        _active, result = asyncio.run(adapter.activate(admission))
    except Exception as e:  # noqa: BLE001
        err_text = f"[error] {type(e).__name__}: {e}"
        if progress_handle is not None:
            # 把占位改成错误消息, adapter 不必再发. 用户看到的是单条
            # 带错误的消息, 没 placeholder 残留.
            _maybe_edit(err_text, force=True)
            return None
        return err_text

    if result is None:
        return "[error] canonical Agent activation produced no result"
    reply_text = (result.final_text or "").strip() or "(empty reply)"
    user_msg_id = result.user_msg_id
    assistant_msg_id = result.assistant_msg_id

    # ---- agent 交出来的文件 → 平台原生附件 -------------------------------
    # reply_text 里的 [attachment: ... @ 路径] 是 send_file 工具登记的.
    # 先把文件真的传上去, 再把标记从"发给平台的那份文本"里拿掉 —— 存进
    # 会话 / 广播给网页的那份 (reply_text) 保留标记, 网页据此画 chip.
    channel_text = _deliver_outbound_files(
        channel, account_id, str(peer_id), reply_text,
    )

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
        if len(channel_text) <= limit:
            _maybe_edit(channel_text, force=True)
        else:
            head = channel_text[: limit - 30]
            tail = channel_text[limit - 30 :]
            _maybe_edit(head + "\n... (continued ↓)", force=True)
            try:
                from openprogram.channels import _transport
                _transport.post_message(
                    channel, account_id, str(peer_id), tail,
                )
            except Exception:
                pass
        return None

    return channel_text


def _deliver_outbound_files(
    channel: str, account_id: str, peer_id: str, reply_text: str,
) -> str:
    """把 reply_text 里的附件标记逐个上传, 返回去掉标记后的平台文本.

    传输层四个平台早就写好了 (``_transport.post_file``), 缺的只是这一步.
    三种结局都不静默:

    * 传成功 — 标记从平台文本里删掉 (文件本身已经在对话里了).
    * 平台不支持 (WeChat iLink 没有文件上传接口) — 标记改写成一句人能读
      的说明加路径, 学 weclaw 的做法; 既不把原始标记漏给用户, 也不假装
      没这回事.
    * 传失败 (网络 / 太大 / 凭据) — 同样改写成一句说明, 并落日志.
    """
    from openprogram.attachments import find_markers
    markers = find_markers(reply_text)
    if not markers:
        return reply_text
    from openprogram.channels import _transport
    out = reply_text
    for marker, name, path in markers:
        try:
            result = _transport.post_file(
                channel, account_id, peer_id, path,
            )
        except Exception as e:  # noqa: BLE001
            result = _transport.SendResult.fail("unknown", f"{type(e).__name__}: {e}")
        if result.ok:
            out = out.replace(marker, "")
            continue
        print(f"[{channel}:{account_id}] send_file {name!r} failed: "
              f"{result.error_kind}: {result.error_detail}")
        note = (f"(附件 {name} 没能发出来，这个平台不支持文件上传；"
                f"文件在 {path})"
                if result.error_kind == "not_supported"
                else f"(附件 {name} 发送失败：{result.error_kind}；文件在 {path})")
        out = out.replace(marker, note)
    return _collapse_blank_lines(out)


def _collapse_blank_lines(text: str) -> str:
    """删掉标记后留下的空行收拢, 别让平台消息末尾挂一串换行."""
    import re
    return re.sub(r"\n{3,}", "\n\n", text).strip() or "(empty reply)"
