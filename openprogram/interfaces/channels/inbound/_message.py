"""中性入站消息结构 — adapter 从 platform-native object 里 parse 出
``ChannelMessage`` 后再喂给 base.handle_inbound.

设计目的: 4 个 adapter 入口用同一个 dataclass, 不再各自 inline 抽
``chat.get("title") or "username" or str(chat_id)`` 那种即兴 fallback 链.
parse 之后的一切 (access 门禁 / 附件下载 / quoted 块 / dispatch) 都在
base 统一处理, adapter 只负责把 platform 字段填进来.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Attachment:
    """一个入站附件的下载描述 — adapter 填元数据, 下载在
    :mod:`._attachments` 统一做 (per-message 线程里, 不堵 poll loop).

    * ``name``    — 原始文件名 (显示 + 落盘名的基础). 拿不到留空.
    * ``mime``    — MIME type. 决定图片是否额外作为 vision 输入.
    * ``url``     — 直接可下载的 URL. Telegram 例外: 留空, 用
                    ``file_id`` 在下载时经 getFile 解析.
    * ``headers`` — 下载要带的 auth header, ``((k, v), ...)`` 元组
                    (frozen dataclass 里不能放 dict). Slack 的
                    ``url_private`` 需要 Bearer bot token.
    * ``file_id`` — Telegram file_id (与空 ``url`` 配对使用).
    * ``size``    — platform 报告的字节数, 0 = 未知. 超过下载上限的
                    直接跳过不发起请求.
    * ``aes_key`` — 下载到的字节要先用这个 AES-128-ECB 密钥 (hex) 解密.
                    只有 WeChat iLink 的 CDN 媒体用 (见 :mod:`._wechat_media`).
    * ``voice``   — 语音消息: 下载后转成文字一起进这一轮
                    (:func:`._attachments.voice_notes`).
    """
    name: str = ""
    mime: str = ""
    url: str = ""
    headers: tuple = ()
    file_id: str = ""
    size: int = 0
    aes_key: str = ""
    voice: bool = False


@dataclass(frozen=True)
class ChannelMessage:
    """一条 inbound 消息的 platform-中性表示.

    必填字段:

    * ``text``       — 消息文本 (UTF-8). 纯附件消息可为空, 但 text 和
                       attachments 至少要有一个, 否则 adapter 直接忽略.
    * ``chat_id``    — platform-native chat / channel / DM identifier
                       的字符串形式.

    可选字段 (adapter 能拿就填, 拿不到留空):

    * ``user_id``       — 发送者 id (空 if anonymous / 系统消息).
                          access 门禁 (allowlist / pairing) 按这个判定,
                          空时退回 chat_id.
    * ``user_display``  — 显示名 (username / global_name / chat title).
                          UI 上给人看的, 不参与 routing.
    * ``message_id``    — platform-native stable message identifier. 用于
                          未配对群聊证据的幂等归档；拿不到时留空.
    * ``chat_type``     — ``direct`` / ``group`` / ``channel`` /
                          ``thread``. 影响 ``dispatch_inbound``
                          ``peer_kind`` 参数.
    * ``ts``            — platform 报告的时间戳 (unix sec). 当前不
                          用, 留给 audit / 排序.
    * ``reply_to_id``   — 这条消息引用 / 回复的另一条消息 platform-id.
    * ``quoted_text``   — 被引用消息的文本 (platform 在入站事件里就带
                          的拿来直接填; Slack thread 父消息 adapter 查
                          一次 API). base 把它组装成 agent 可见的
                          quoted 块.
    * ``thread_id``     — thread / 楼层 id (Slack thread_ts, Discord
                          thread channel, 等).
    * ``parent_chat_id``— 这条消息所在 thread 的父频道 (Discord thread 的
                          ``chat_id`` 是它自己的 id). 群名单先看 thread
                          自己的覆盖, 再看父频道.
    * ``attachments``   — tuple of :class:`Attachment`. base 在
                          dispatch 前统一下载落盘.
    * ``mentioned``     — 这条消息是冲机器人说的: @了它、命中提及模式,
                          或回复了它的消息. adapter 判定并把提及从
                          ``text`` 里剥掉; 群聊 ``mention`` 模式只让这种
                          消息开启一轮 (见 :mod:`._room`).
    * ``from_bot``      — 发送者是机器人账号. base 直接丢弃, 防止两个
                          机器人在群里互相回复.
    """
    text: str
    chat_id: str
    user_id: str = ""
    user_display: str = ""
    message_id: str = ""
    chat_type: str = "direct"
    ts: float = 0.0
    reply_to_id: str = ""
    quoted_text: str = ""
    thread_id: str = ""
    parent_chat_id: str = ""
    attachments: tuple = field(default_factory=tuple)
    mentioned: bool = False
    from_bot: bool = False

    @property
    def is_dm(self) -> bool:
        """True iff 这条消息来自 1-on-1 私信."""
        return self.chat_type == "direct"
