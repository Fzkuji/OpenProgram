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

import abc
import threading
from dataclasses import dataclass
from typing import Optional


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
        """

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
        """统一入站处理: 每消息起一个 daemon 线程跑 dispatch.

        Per-message 线程是必须的 — 函数在 turn 里停在 runtime.ask 时不能
        堵住 adapter 的 poll loop / 事件回调, 否则用户自己的 /answer 回复
        永远进不来, 等待自死锁.

        dispatch_inbound 返回字符串 (progress streaming 降级 / 关闭) 时
        经 :meth:`send_text` 回发 — 统一走 _transport, chunking 与错误
        分类都在那一层, adapter 不再持有 platform SDK 的直发副本.
        """
        threading.Thread(
            target=self._dispatch_and_reply, args=(ch_msg,), daemon=True,
        ).start()

    def _dispatch_and_reply(self, ch_msg) -> None:
        peer_id = self.peer_id_for(ch_msg)
        snippet = ch_msg.text[:60] + ("..." if len(ch_msg.text) > 60 else "")
        who = ch_msg.user_display or ch_msg.user_id or peer_id
        print(f"[{self.platform_id}:{self.account_id}] <{who}> {snippet}")

        from openprogram.channels._conversation import dispatch_inbound
        reply_text = dispatch_inbound(
            channel=self.platform_id,
            account_id=self.account_id,
            peer_kind=ch_msg.chat_type,
            peer_id=peer_id,
            user_text=ch_msg.text,
            user_display=ch_msg.user_display or peer_id,
            progress_stream=self.progress_stream,
        )
        # progress streaming 走通时 reply 已 edit 进占位消息, 返回 None.
        if reply_text is not None:
            result = self.send_text_full(peer_id, reply_text)
            if not result.ok:
                print(f"[{self.platform_id}:{self.account_id}] send failed: "
                      f"{result.error_kind}: {result.error_detail}")

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

    def send_text_full(self, target: str, text: str):
        """跟 :meth:`send_text` 一样但返回完整 :class:`SendResult`."""
        from openprogram.channels import _transport
        return _transport.post_message(
            self.platform_id, self.account_id, target, text,
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
        from openprogram.channels._transport import SendResult
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
        from openprogram.channels import _transport
        return _transport.patch_message(
            handle.platform, handle.account_id,
            handle.target, handle.message_id, new_text,
        )
