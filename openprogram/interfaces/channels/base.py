"""Channel 抽象 — 入口 B 接口定义 + 入站 loop 抽象方法.

入口 B (有状态、保留 message_id 可以后续 edit) 主要给两类调用者用:

* dispatcher 在跑流式 turn 时, 先 ``adapter.send_text(target, "🤔...")``
  拿 handle, 然后 tool 事件触发时 ``adapter.edit_text(handle, "🔧 bash...")``,
  最后用最终 reply 收尾 — 即 channel 侧 progress streaming.
* dispatcher 路径下 channel adapter 自己在 ``on_message`` 里收到用户消息后,
  也可以用 send_text/edit_text 跟用户来回, 而不必各自维护一份 platform SDK
  调用代码.

底层 HTTP 实现走 :mod:`._transport`, 跟入口 A (:mod:`.outbound`) 共用.
Adapter 子类如果想用 platform-native SDK 替代 raw HTTP (比如 discord.py
的 mention 解析、附件上传) 可以 override ``send_text`` / ``edit_text``.

注意: ``run(stop)`` 仍然是抽象方法 — 每个 adapter 的入站事件循环
(discord.py / slack_sdk / 长轮询) 形态差异太大, 没法在 base 里统一.
"""
from __future__ import annotations

import logging
import abc
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from openprogram.host.security.identity_header import (
    IDENTITY_HEADER_PART_MAX_CHARS,
    normalize_identity_header_part,
)
from openprogram.interfaces.channels import _heartbeats

_log = logging.getLogger(__name__)


#: quoted 文本进 agent 上下文的截断上限 — 被引用消息只是背景, 不值得
#: 占大量 token.
QUOTED_MAX_CHARS = 500

#: 发言人标签里显示名的截断上限. 显示名是发信人自己在平台上填的, 长度
#: 由平台管, 这里只挡住病态长度不让它盖过消息本身.
SENDER_NAME_MAX_CHARS = IDENTITY_HEADER_PART_MAX_CHARS


def _quoted_block(quoted: str) -> str:
    """被引用消息 → agent 可见的统一 quoted 块 (markdown 引用格式)."""
    q = quoted.strip()
    if len(q) > QUOTED_MAX_CHARS:
        q = q[:QUOTED_MAX_CHARS] + "…"
    lines = "\n".join("> " + ln for ln in q.splitlines())
    return f"[quoted message]\n{lines}"


def _one_line(value: str) -> str:
    """Compatibility wrapper for the shared runtime-header normalizer."""
    return normalize_identity_header_part(value)


class _TypingIndicator:
    """一轮进行期间在聊天里显示"正在输入", 按平台的有效期定时刷新.

    平台不支持 (Slack) 或第一次发送失败就安静退出 — 这只是提示, 不能
    影响回复本身.
    """

    def __init__(self, platform: str, account_id: str, target: str,
                 thread_id: str = "") -> None:
        self.platform = platform
        self.account_id = account_id
        self.target = target
        self.thread_id = thread_id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    def start(self) -> None:
        from openprogram.interfaces.channels._transport import TYPING_REFRESH_SECONDS
        interval = TYPING_REFRESH_SECONDS.get(self.platform)
        if not interval:
            return
        self._thread = threading.Thread(
            target=self._run, args=(interval,), daemon=True,
        )
        self._thread.start()

    def _run(self, interval: float) -> None:
        from openprogram.interfaces.channels import _transport
        where = {"thread_id": self.thread_id} if self.thread_id else {}
        while not self._stop.is_set():
            try:
                result = _transport.send_typing(
                    self.platform, self.account_id, self.target, **where,
                )
            except Exception:  # noqa: BLE001
                return
            if not result.ok:
                return
            self._started = True
            if self._stop.wait(interval):
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._started:
            from openprogram.interfaces.channels import _transport
            try:
                _transport.stop_typing(self.platform, self.account_id, self.target)
            except Exception:  # noqa: BLE001
                # The platform clears the typing indicator on its own timeout.
                _log.debug("[%s:%s] stop_typing failed", self.platform,
                           self.account_id, exc_info=True)


class _InboundOrder:
    """同一 peer 的消息按到达顺序开启轮次.

    每条消息一个线程, 带附件的那条下载慢, 后面的文字会抢先. 这里只管
    "下载到拿到 session 锁" 这一段: 拿到锁后就放下一条进来; 不开启轮次的
    消息 (门禁挡下 / 记成群历史) 随时退出, 不等前面的. 命令不排队.
    """

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._queues: dict[tuple[str, str, str], deque[object]] = {}

    def enter(self, key: tuple[str, str, str]) -> "_InboundTicket":
        ticket = _InboundTicket(self, key)
        with self._changed:
            self._queues.setdefault(key, deque()).append(ticket)
        return ticket

    def wait_for_turn(self, ticket: "_InboundTicket") -> None:
        with self._changed:
            self._changed.wait_for(
                lambda: self._queues.get(ticket.key, (ticket,))[0] is ticket
            )

    def leave(self, ticket: "_InboundTicket") -> None:
        with self._changed:
            queue = self._queues.get(ticket.key)
            if queue is None or ticket not in queue:
                return
            queue.remove(ticket)
            if not queue:
                del self._queues[ticket.key]
            self._changed.notify_all()


@dataclass(frozen=True, eq=False)
class _InboundTicket:
    order: _InboundOrder
    key: tuple[str, str, str]

    def wait_for_turn(self) -> None:
        self.order.wait_for_turn(self)

    def leave(self) -> None:
        self.order.leave(self)


_INBOUND_ORDER = _InboundOrder()


@dataclass(frozen=True)
class MessageHandle:
    """指向一条已发出去的消息 — 后续可以拿来 edit.

    四字段都是字符串, 方便跨进程序列化 (写进文件 / 通过 WS 传) — 总管
    程序持有句柄, 别的进程也能拿同样的 handle 去调 edit_text.

    * ``platform``    — "telegram" / "discord" / "slack" / "wechat"
    * ``account_id``  — 哪个账号发的 (multi-account 区分)
    * ``target``      — 收信人语义按 platform 不同, 跟 outbound.send
                        的 user_id 参数一致
    * ``message_id``  — platform-native 字符串. WeChat 是空字符串
                        (iLink 不支持 edit, handle 没法用来 patch)
    """
    platform: str
    account_id: str
    target: str
    message_id: str

    @property
    def editable(self) -> bool:
        """该 handle 是否能用来 edit_text. WeChat 永远 False."""
        return bool(self.message_id) and self.platform != "wechat"


class Channel(abc.ABC):
    """每个 platform adapter 继承这个类.

    ``platform_id`` 子类必须设 (字符串, 跟 ``_transport`` / outbound 用
    的 channel 名一致 — "telegram" / "discord" / "slack" / "wechat").
    """
    platform_id: str = ""

    def __init__(self, account_id: str = "default") -> None:
        self.account_id = account_id

    #: dispatch_inbound 的 progress_stream 参数. WeChat 关掉 (iLink 不能
    #: edit, 占位消息会变成孤儿"⏳"残留), 其他 platform 默认开.
    progress_stream: bool = True

    @abc.abstractmethod
    def run(self, stop: threading.Event) -> None:
        """跑入站事件循环直到 ``stop`` 被 set.

        实现细节按 platform 不同 (discord.py Gateway / Slack Socket Mode
        / Telegram long-poll / WeChat iLink long-poll), 在各 adapter 文件
        自己写. 一般只做两件事:
          * 长连接 / 长轮询拿入站消息, parse 成 ChannelMessage
          * 调 :meth:`handle_inbound` — 线程派发 / dispatch / 降级回发
            都在 base 统一处理

        返回语义 (``run_forever`` 依赖):
          * 抛异常          — 瞬态故障 (断网 / gateway 掉线), 会被退避重连
          * 正常 return     — 永久停止 (凭据失效需人工重登), 不再重启
        """

    #: run_forever 的重连退避参数 — 初始等待秒数, 指数翻倍到上限.
    RESTART_BACKOFF = 5.0
    RESTART_BACKOFF_MAX = 300.0
    #: 一次运行存活这么久 (秒) 视为连接曾经健康.
    HEALTHY_RUN_SECONDS = 60.0

    def run_forever(self, stop: threading.Event) -> None:
        """带自动重连的入站循环 — worker 用这个入口跑 adapter.

        :meth:`run` 异常退出 (网络断 / gateway 掉线 / SDK 崩溃) 时指数
        退避重连: 5s 起步翻倍, 封顶 300s; 一次运行存活超过 60s 视为
        连接曾经健康, 退避重置回 5s. 每次崩溃和重连都落日志.

        :meth:`run` **正常 return** 视为永久停止 (adapter 自己判断没救
        了, e.g. wechat token 失效要求重新扫码), 不再重启 — 避免对着
        失效凭据无限空转.
        """
        tag = f"{self.platform_id}:{self.account_id}"
        backoff = self.RESTART_BACKOFF
        while not stop.is_set():
            started = time.time()
            try:
                self.run(stop)
            except Exception as e:  # noqa: BLE001
                _log.error("[%s] adapter crashed: %s: %s", tag, type(e).__name__, e,
                           exc_info=True)
            else:
                if not stop.is_set():
                    _log.error("[%s] adapter exited on its own — not restarting "
                               "(operator action needed)", tag)
                return
            if stop.is_set():
                return
            if time.time() - started >= self.HEALTHY_RUN_SECONDS:
                backoff = self.RESTART_BACKOFF
            _log.info("[%s] reconnecting in %.0fs", tag, backoff)
            _heartbeats.record_retry(
                self.platform_id, self.account_id,
                backoff + self.HEALTHY_RUN_SECONDS,
            )
            if stop.wait(backoff):
                return
            backoff = min(backoff * 2, self.RESTART_BACKOFF_MAX)

    # ------------------------------------------------------------------
    # 入站统一处理 — adapter parse 完调这个, 不再各抄一遍线程+降级逻辑
    # ------------------------------------------------------------------

    def peer_id_for(self, ch_msg) -> str:
        """从 :class:`ChannelMessage` 算 dispatch 用的 peer_id — 同时也是
        降级回发的 outbound target. 默认 chat_id (telegram 群共享 /
        wechat 单聊). Discord / Slack override 成 ``{chat_id}_{user_id}``
        让同一 channel 里的不同用户各占一个 session."""
        return ch_msg.chat_id

    def handle_inbound(self, ch_msg) -> None:
        """统一入站处理: 每消息起一个 daemon 线程跑 access 门禁 →
        附件下载 → quoted 块组装 → dispatch.

        Per-message 线程是必须的 — 函数在 turn 里停在 runtime.ask 时不能
        堵住 adapter 的 poll loop / 事件回调, 否则用户自己的 /answer 回复
        永远进不来, 等待自死锁. 附件下载同理 (可能几十 MB).

        dispatch_inbound 返回字符串 (progress streaming 降级 / 关闭) 时
        经 :meth:`send_text` 回发 — 统一走 _transport, chunking / 格式
        渲染 / 限流重试都在那一层, adapter 不再持有 platform SDK 的直发
        副本.

        排队号在这里、adapter 的收消息顺序上领, 线程起来之后再领就晚了.
        命令 (/answer 等) 不排队: 一轮停在提问上时前面的消息都在等它.
        """
        from openprogram.interfaces.channels import _room

        ticket = None
        if not _room.command_verb(ch_msg.text):
            ticket = _INBOUND_ORDER.enter(
                (self.platform_id, self.account_id, self.peer_id_for(ch_msg)),
            )
        worker = threading.Thread(
            target=self._dispatch_in_order, args=(ch_msg, ticket), daemon=True,
        )
        try:
            worker.start()
        except BaseException:
            # 线程没起来就没人还这个号, 这个 peer 后面的消息会一直等下去.
            if ticket is not None:
                ticket.leave()
            raise

    def _dispatch_in_order(self, ch_msg, ticket) -> None:
        try:
            self._dispatch_and_reply(ch_msg, ticket)
        finally:
            if ticket is not None:
                ticket.leave()

    def _dispatch_and_reply(self, ch_msg, ticket=None) -> None:
        from openprogram.interfaces.channels import _room

        peer_id = self.peer_id_for(ch_msg)
        snippet = ch_msg.text[:60] + ("..." if len(ch_msg.text) > 60 else "")
        safe_display = _one_line(ch_msg.user_display)
        who = safe_display or ch_msg.user_id or peer_id
        tag = f"[{self.platform_id}:{self.account_id}]"
        # 别的机器人发的消息不进 agent — 两个机器人在一个群里会互相回复
        # 到有人把它们停下为止.
        if ch_msg.from_bot:
            return
        if not _room.first_delivery(self.platform_id, self.account_id,
                                    ch_msg.chat_id, ch_msg.message_id):
            return

        # ---- access 门禁: 未知发信人不进 agent --------------------------
        # 配对确认只能走本机 CLI/webui (_access 模块 docstring), 这里
        # 只可能发配对码回执, 绝不放行. 群里不配对: 不在名单上的人什么
        # 反应都没有 (id 记进日志, 主人要加人时能查到).
        from openprogram.interfaces.channels import _access
        stable_sender_id = ch_msg.user_id or (
            ch_msg.chat_id if ch_msg.is_dm else ""
        )
        in_room = _room.is_room(ch_msg.chat_type)
        group_level = None
        if in_room:
            group_level = _access.group_sender_access(
                self.platform_id, self.account_id, ch_msg.chat_id,
                stable_sender_id, parent_chat_id=ch_msg.parent_chat_id,
            )
            if group_level is None:
                _log.info("%s sender %s is on no list for group %s; ignored",
                          tag, stable_sender_id or "(no id)", ch_msg.chat_id)
                return
        else:
            access_decision = _access.decide_inbound_sender(
                self.platform_id, self.account_id,
                stable_sender_id,
                display=safe_display,
            )
            if not access_decision.allowed:
                _log.info("%s <%s> blocked by access policy (%s)",
                          tag, who, access_decision.reason_code)
                if access_decision.reply:
                    self._reply(peer_id, access_decision.reply, ch_msg.thread_id)
                return
        _log.info("%s <%s> %s", tag, who, snippet)

        # ---- 房间层: 命令 / @提及门槛 / 限流 -----------------------------
        room_key = (self.platform_id, self.account_id, ch_msg.chat_id)
        verb = _room.command_verb(ch_msg.text)
        if verb in ("/new", "/reset", "/activation", "/memory", "/stop", "/help"):
            # 只能聊天的人不能改全群的设置, 也不能替全群清空对话或停掉任务.
            if in_room and group_level != "full":
                self._reply(peer_id, {
                    "/stop": "你在这个群里只能聊天，不能停止正在进行的任务。",
                    "/help": "你在这个群里只能聊天，这些命令需要完全访问权限。",
                }.get(verb, "你在这个群里只能聊天，不能改设置或开始新对话。"),
                            ch_msg.thread_id)
                return
            self._reply(peer_id, self._room_command(
                verb, ch_msg, peer_id, room_key, in_room,
            ), ch_msg.thread_id)
            return
        # ambient: 群里没点名机器人的消息照样开启一轮, 但只是房间事件 ——
        # 最终回复不发进群, 不加确认表情, 不显示"正在输入".
        ambient = False
        if in_room and not verb:
            entry = _room.PendingEntry(
                speaker_id=stable_sender_id, speaker_display=safe_display,
                text=ch_msg.text or "(attachment)", ts=ch_msg.ts or time.time(),
            )
            mode = _room.activation(self.platform_id, self.account_id,
                                    ch_msg.chat_id)
            if mode == "mention" and not ch_msg.mentioned:
                _room.record_pending(room_key, entry)
                return
            ambient = mode == "ambient" and not ch_msg.mentioned
            if not _room.allow_turn(room_key):
                _log.info("%s room %s over its turn limit; message kept as pending history",
                          tag, ch_msg.chat_id)
                if ch_msg.text or ch_msg.attachments:
                    _room.record_pending(room_key, entry)
                return

        if ticket is not None:
            ticket.wait_for_turn()

        # ---- 附件下载 + quoted 块 → agent 可见的 user_text ---------------
        user_text = ch_msg.text
        turn_attachments: list[dict] = []
        if ch_msg.attachments:
            from openprogram.interfaces.channels import _attachments
            failed: list[dict] = []
            saved = _attachments.download_inbound(
                self.platform_id, self.account_id, ch_msg.attachments,
                failed=failed,
            )
            turn_attachments = _attachments.to_turn_attachments(saved)
            # 说文字的放前面, 文件标记放最后: 网页把标记渲染成 chip.
            sections = [
                user_text,
                *_attachments.voice_notes(saved),
                "\n".join(_attachments.failure_notes(failed)),
                "\n".join(_attachments.attachment_notes(saved)),
            ]
            user_text = "\n\n".join(part for part in sections if part)
        # 命令 (/answer · /decline) 原样交给 dispatch 解析, 不拼上下文块.
        if ch_msg.quoted_text and not verb:
            user_text = _quoted_block(ch_msg.quoted_text) + "\n\n" + user_text
        if in_room and not verb:
            pending = _room.take_pending(room_key)
            if pending:
                user_text = _room.render_pending(pending) + "\n\n" + user_text
        # 只 @了机器人, 没引用、没新的群消息: 没有内容可回应, 不开空轮次.
        if not user_text.strip():
            if in_room:
                _room.give_back_turn(room_key)
            return
        if ambient:
            user_text = f"{_room.ROOM_EVENT_HEADER}\n{user_text}"

        # 正文保持发信人原文；发言人只走独立 speaker 字段, 模型从
        # 输入信封 (authority.render_model_input) 读, 网页从消息元数据读.

        if not verb and not ambient:
            self._ack(ch_msg)
        from openprogram.interfaces.channels._conversation import dispatch_inbound
        typing = _TypingIndicator(self.platform_id, self.account_id, peer_id,
                                  ch_msg.thread_id or "")
        if not verb and not ambient:
            typing.start()

        def keep_room_history() -> None:
            # 这一轮没跑: 取走的群消息放回去, 下一轮还能看到. 没点名的房间
            # 事件没人会重发, 也留下; 点名的那条由发信人收到"忙"后重发.
            kept = [*pending, entry] if ambient else pending
            if kept:
                _room.restore_pending(room_key, kept)

        try:
            reply_text = dispatch_inbound(
                channel=self.platform_id,
                account_id=self.account_id,
                peer_kind=ch_msg.chat_type,
                peer_id=peer_id,
                user_text=user_text,
                user_display=safe_display or peer_id,
                speaker_id=stable_sender_id or None,
                speaker_display=safe_display or None,
                progress_stream=self.progress_stream,
                block_stream=not self.progress_stream,
                attachments=turn_attachments or None,
                ambient=ambient,
                room_full_access=group_level == "full",
                memory_mode=(_room.memory_mode(self.platform_id,
                                               self.account_id, ch_msg.chat_id)
                             if in_room else "on"),
                thread_id=ch_msg.thread_id or None,
                on_busy=keep_room_history if in_room and not verb else None,
                on_session_locked=ticket.leave if ticket is not None else None,
            )
        finally:
            typing.stop()
        # 占位消息 edit 或分段发送走通时 reply 已经送达, 返回 None.
        if reply_text is not None:
            result = self._reply(peer_id, reply_text, ch_msg.thread_id)
            if not result.ok:
                _log.warning("%s send failed: %s: %s",
                             tag, result.error_kind, result.error_detail)

    def _room_command(self, verb: str, ch_msg, peer_id: str,
                      room_key: tuple[str, str, str], in_room: bool) -> str:
        """/new · /reset · /activation · /memory · /stop · /help — 回执文本."""
        from openprogram.interfaces.channels import _room
        if verb == "/help":
            return _room.HELP_TEXT
        if verb == "/stop":
            from openprogram.interfaces.channels._conversation import stop_current_work
            return stop_current_work(
                channel=self.platform_id, account_id=self.account_id,
                peer_kind=ch_msg.chat_type, peer_id=peer_id,
            )
        if verb in ("/new", "/reset"):
            from openprogram.interfaces.channels._conversation import (
                BUSY_REPLY, start_new_conversation,
            )
            receipt = start_new_conversation(
                channel=self.platform_id, account_id=self.account_id,
                peer_kind=ch_msg.chat_type, peer_id=peer_id,
            )
            if receipt != BUSY_REPLY:
                _room.clear_pending(room_key)
            return receipt
        arg = _room.command_arg(ch_msg.text).lower()
        if verb == "/memory":
            if not in_room:
                return "私聊沿用 agent 自己的记忆设置，不需要设置。"
            return self._memory_command(ch_msg.chat_id, arg)
        if not in_room:
            return "私聊里我会回复每一条消息，不需要设置。"
        labels = {"mention": "只在 @我 或回复我时回应",
                  "always": "回应每一条消息",
                  "ambient": "每条消息都看，有话要说时才开口"}
        usage = "/activation mention、/activation always 或 /activation ambient"
        if not arg:
            mode = _room.activation(self.platform_id, self.account_id,
                                    ch_msg.chat_id)
            return f"当前模式：{mode}（{labels[mode]}）。用 {usage} 切换。"
        if arg not in _room.ACTIVATION_MODES:
            return f"用法：{usage}"
        _room.set_activation(self.platform_id, self.account_id,
                             ch_msg.chat_id, arg)
        return f"✓ 已切换为 {arg}：{labels[arg]}。"

    def _memory_command(self, chat_id: str, arg: str) -> str:
        """/memory on|read|off — 这个群的轮次能否读写主人的记忆."""
        from openprogram.interfaces.channels import _room
        labels = {"on": "按 agent 的记忆设置读写",
                  "read": "只读记忆，不写入",
                  "off": "不读也不写记忆"}
        usage = "/memory on、/memory read 或 /memory off"
        if not arg:
            mode = _room.memory_mode(self.platform_id, self.account_id, chat_id)
            return f"这个群的记忆：{mode}（{labels[mode]}）。用 {usage} 切换。"
        if arg not in _room.MEMORY_MODES:
            return f"用法：{usage}"
        _room.set_memory_mode(self.platform_id, self.account_id, chat_id, arg)
        return f"✓ 记忆已设为 {arg}：{labels[arg]}。"

    def _ack(self, ch_msg) -> None:
        """账号开了 ``ack_reaction`` 时给接手的消息加一个 👀."""
        if not ch_msg.message_id:
            return
        from openprogram.interfaces.channels import _transport
        from openprogram.interfaces.channels.accounts import get_settings
        if get_settings(self.platform_id, self.account_id).get(
                "ack_reaction", "off") != "on":
            return
        result = _transport.add_reaction(
            self.platform_id, self.account_id,
            ch_msg.chat_id, ch_msg.message_id,
        )
        if not result.ok and result.error_kind != "not_supported":
            _log.warning("[%s:%s] ack reaction failed: %s",
                         self.platform_id, self.account_id, result.error_kind)

    # ------------------------------------------------------------------
    # 出站接口 — 默认走 _transport, 子类可 override 用 platform-native SDK
    # ------------------------------------------------------------------

    def send_text(self, target: str, text: str) -> Optional[MessageHandle]:
        """发一条消息. 成功返回 :class:`MessageHandle` (可用来后续 edit),
        失败返回 ``None``.

        想拿结构化失败原因 (error_kind / retryable) 用
        :meth:`send_text_full`.

        Default 实现走 :func:`._transport.post_message`, 跟 outbound.send
        是同一份底层. 子类想用 platform-native SDK 替代 (mention 解析、
        附件上传等) 可以 override.
        """
        result = self.send_text_full(target, text)
        if not result.ok:
            return None
        return MessageHandle(
            platform=self.platform_id,
            account_id=self.account_id,
            target=target,
            message_id=result.message_id,
        )

    def send_text_full(self, target: str, text: str,
                       thread_id: Optional[str] = None):
        """跟 :meth:`send_text` 一样但返回完整 :class:`SendResult`.
        ``thread_id`` 发进 thread (见 :func:`._transport.post_message`)."""
        from openprogram.interfaces.channels import _transport
        if thread_id:
            return _transport.post_message(
                self.platform_id, self.account_id, target, text,
                thread_id=thread_id,
            )
        return _transport.post_message(
            self.platform_id, self.account_id, target, text,
        )

    def _reply(self, target: str, text: str, thread_id: str = ""):
        """回发到入站消息所在的 thread; 不在 thread 里时就是
        ``send_text_full(target, text)``."""
        if thread_id:
            return self.send_text_full(target, text, thread_id=thread_id)
        return self.send_text_full(target, text)

    def send_file(self, target: str, path: str, caption: str = "",
                  thread_id: Optional[str] = None):
        """把本地文件发给 ``target``. 返回 :class:`SendResult`.

        走 :func:`._transport.post_file`. 平台不支持时返回
        ``not_supported`` — 调用方自行降级 (e.g. 改发带路径的文本).
        """
        from openprogram.interfaces.channels import _transport
        if thread_id:
            return _transport.post_file(
                self.platform_id, self.account_id, target, path, caption,
                thread_id=thread_id,
            )
        return _transport.post_file(
            self.platform_id, self.account_id, target, path, caption,
        )

    def edit_text(self, handle: MessageHandle, new_text: str) -> bool:
        """把 ``handle`` 指向的消息改成 ``new_text``. 返回 True/False.

        WeChat 永远返回 False (iLink 不支持 edit). 其他 platform 走
        :func:`._transport.patch_message`. 想拿结构化失败原因用
        :meth:`edit_text_full`.

        Handle 的 ``platform`` 字段必须跟当前 adapter 一致 — 不允许跨
        platform edit (那是 multi-adapter 协调的范畴, 不归 base 管).
        """
        return self.edit_text_full(handle, new_text).ok

    def edit_text_full(self, handle: MessageHandle, new_text: str):
        """跟 :meth:`edit_text` 一样但返回完整 :class:`SendResult`."""
        from openprogram.interfaces.channels._transport import SendResult
        if not handle.editable:
            return SendResult.fail(
                "not_supported",
                f"{handle.platform} message {handle.message_id!r} not editable",
            )
        if handle.platform != self.platform_id:
            return SendResult.fail(
                "bad_target",
                f"cross-platform edit refused: handle.platform={handle.platform!r} "
                f"vs adapter.platform_id={self.platform_id!r}",
            )
        from openprogram.interfaces.channels import _transport
        return _transport.patch_message(
            handle.platform, handle.account_id,
            handle.target, handle.message_id, new_text,
        )
