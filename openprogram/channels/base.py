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

    @abc.abstractmethod
    def run(self, stop: threading.Event) -> None:
        """跑入站事件循环直到 ``stop`` 被 set.

        实现细节按 platform 不同 (discord.py Gateway / Slack Socket Mode
        / Telegram long-poll / WeChat iLink long-poll), 在各 adapter 文件
        自己写. 一般会:
          * 长连接 / 长轮询拿入站消息
          * 调 ``dispatch_inbound`` 喂给 agent
          * 把 agent reply 发回去 (现在仍用各自 platform SDK, 后续可改成
            self.send_text)
        """

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
