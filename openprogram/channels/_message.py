"""中性入站消息结构 — adapter 从 platform-native object 里 parse 出
``ChannelMessage`` 后再喂给 dispatch_inbound.

设计目的:

* **代码一致性**: 4 个 adapter 入口现在用同一个 dataclass, 不再各自
  inline 抽 ``chat.get("title") or "username" or str(chat_id)`` 那种
  即兴 fallback 链.
* **铺路富 message**: ``reply_to_id`` / ``thread_id`` / ``attachments``
  字段当前 dispatch_inbound 不消费, 但 adapter 已经可以抽出来 — 等
  将来支持 reply quote / 附件读取时, parse 这步已经写好.

dispatch_inbound 签名暂时不动 (仍是 6 个 keyword args). adapter 构造
ChannelMessage 后展开传给 dispatch_inbound. 等富 message 字段真用上
再决定要不要让 dispatch_inbound 直接接 ChannelMessage.

修 audit 缺陷 4 (ChannelMessage 中性结构缺失).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelMessage:
    """一条 inbound 消息的 platform-中性表示.

    必填字段:

    * ``text``       — 消息文本 (UTF-8). 空文本 adapter 应直接忽略不
                       进 dispatch.
    * ``chat_id``    — platform-native chat / channel / DM identifier
                       的字符串形式.

    可选字段 (adapter 能拿就填, 拿不到留空):

    * ``user_id``       — 发送者 id (空 if anonymous / 系统消息).
    * ``user_display``  — 显示名 (username / global_name / chat title).
                          UI 上给人看的, 不参与 routing.
    * ``chat_type``     — ``direct`` / ``group`` / ``channel`` /
                          ``thread``. 影响 ``dispatch_inbound``
                          ``peer_kind`` 参数.
    * ``ts``            — platform 报告的时间戳 (unix sec). 当前不
                          用, 留给 audit / 排序.
    * ``reply_to_id``   — 这条消息引用 / 回复的另一条消息 platform-id.
                          dispatch_inbound 当前不消费, 留给 reply
                          quote 功能.
    * ``thread_id``     — thread / 楼层 id (Slack thread_ts, Discord
                          thread channel, 等). dispatch_inbound 当前
                          不消费, 留给 thread-scoped session 隔离
                          (audit 缺陷 8 的未来工作).
    * ``attachments``   — tuple of (url, mime_type) pair. dispatch_inbound
                          当前不消费, 留给 attachment 读取功能.
    """
    text: str
    chat_id: str
    user_id: str = ""
    user_display: str = ""
    chat_type: str = "direct"
    ts: float = 0.0
    reply_to_id: str = ""
    thread_id: str = ""
    attachments: tuple = field(default_factory=tuple)

    @property
    def is_dm(self) -> bool:
        """True iff 这条消息来自 1-on-1 私信."""
        return self.chat_type == "direct"
