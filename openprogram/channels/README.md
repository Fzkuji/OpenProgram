# `openprogram/channels/`

> Chat-channel registry.

## Overview

A *channel* is a platform (wechat, telegram, discord, slack — plus any
externally-registered platform).
A *channel account* is one specific bot login of that platform — e.g.
a single Telegram bot token, one WeChat QR-scan session.

Each channel class takes ``account_id`` in ``__init__`` and reads
its credentials from
``<state>/channels/<channel>/accounts/<account_id>/credentials.json``.

Built-in platforms (telegram / discord / slack / wechat) ship硬-coded
in :data:`CHANNEL_CLASSES`. External plugins register additional
platforms via:

1. ``importlib.metadata`` entry-points under group
   ``openprogram.channels``. Plugin ``pyproject.toml`` example::

        [project.entry-points."openprogram.channels"]
        whatsapp = "my_pkg.whatsapp:WhatsAppChannel"

2. Imperative :func:`register_channel` call (在 plugin 的
   ``hooks.session_start`` 或 ``__init__`` 里). 适合代码内动态注册.

修 audit 缺陷 10: 之前 CHANNEL_CLASSES 是硬编码 dict, 加新 platform
要改源码 + 4 个 helper. 现在 plugin 可以直接挂.

## Files in this directory

- **`_broadcast.py`** — Channel turn 完成后给 webui 推 WS event
- **`_conversation.py`** — Inbound-message → agent-session dispatcher
- **`_heartbeats.py`** — Channel adapter heartbeat registry
- **`_message.py`** — 中性入站消息结构
- **`_session_routing.py`** — Channel session 路由
- **`_session_store.py`** — Channel session 存储
- **`_transport.py`** — 共享底层
- **`accounts.py`** — Multi-account channel credential store
- **`base.py`** — Channel 抽象
- **`bindings.py`** — Channel → Agent routing
- **`discord.py`** — Discord bot channel via ``discord.py`` (Gateway WebSocket)
- **`outbound.py`** — Outbound
- **`setup.py`** — Channels setup wizard
- **`slack.py`** — Slack bot channel via Socket Mode (``slack_sdk``)
- **`telegram.py`** — Telegram bot channel via the public Bot API (long-polling)
- **`wechat.py`** — WeChat bot channel via Tencent's iLink bot API
- **`worker.py`** — Backward-compatible channel worker imports

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
