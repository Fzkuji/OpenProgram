"""Chat-channel registry.

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
"""
from __future__ import annotations

from typing import Any

from openprogram.channels.base import Channel
from openprogram.channels.implementations.telegram import TelegramChannel
from openprogram.channels.implementations.discord import DiscordChannel
from openprogram.channels.implementations.slack import SlackChannel
from openprogram.channels.implementations.wechat import WechatChannel


# 内置 platform — 永远存在, plugin 无法 override 这些名字 (重名 plugin
# entry-point 会被无声忽略, 因为 built-in 优先).
_BUILTIN_CHANNEL_CLASSES: dict[str, type[Channel]] = {
    "telegram": TelegramChannel,
    "discord": DiscordChannel,
    "slack": SlackChannel,
    "wechat": WechatChannel,
}

# Plugin 通过 entry_points 或 register_channel 加进来的 platform.
# Built-in + plugin 合在一起对外暴露成 CHANNEL_CLASSES.
_PLUGIN_CHANNEL_CLASSES: dict[str, type[Channel]] = {}

_ENTRY_POINTS_LOADED = False


def _load_plugin_entry_points() -> None:
    """从 importlib.metadata 扫 ``openprogram.channels`` entry-point.

    每次启动只扫一次 (用 module-level _ENTRY_POINTS_LOADED 标志). 加载
    失败的 entry-point 静默跳过 — 一个坏 plugin 不应该拖垮整个 channels
    模块导入.
    """
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True

    try:
        from importlib.metadata import entry_points
    except ImportError:
        return

    try:
        eps = entry_points(group="openprogram.channels")
    except TypeError:
        # Python < 3.10 兼容: entry_points() 不接 group 参数, 要后过滤
        try:
            all_eps = entry_points()
            eps = all_eps.get("openprogram.channels", []) if hasattr(all_eps, "get") else []
        except Exception:
            return
    except Exception:
        return

    for ep in eps:
        name = ep.name
        if name in _BUILTIN_CHANNEL_CLASSES:
            # built-in 优先, 跳过同名 plugin (而不是 override)
            continue
        if name in _PLUGIN_CHANNEL_CLASSES:
            continue
        try:
            cls = ep.load()
        except Exception as e:  # noqa: BLE001
            print(
                f"[channels] entry-point {name!r} failed to load: "
                f"{type(e).__name__}: {e}"
            )
            continue
        if not isinstance(cls, type) or not issubclass(cls, Channel):
            print(
                f"[channels] entry-point {name!r} loaded {cls!r}, not a "
                f"Channel subclass — skipped"
            )
            continue
        _PLUGIN_CHANNEL_CLASSES[name] = cls


def register_channel(name: str, cls: type[Channel]) -> None:
    """Imperative 注册一个 channel platform.

    plugin 在自己的 ``hooks.session_start`` / ``__init__`` 里调:

        from openprogram.channels import register_channel
        from my_pkg.whatsapp import WhatsAppChannel
        register_channel("whatsapp", WhatsAppChannel)

    跟 entry-point 等价, 适合不想或不能写 pyproject entry-point 的场景
    (e.g. 在 jupyter 里临时挂一个测试 channel). 内置 platform 不能被
    override — 重名会被无声忽略.
    """
    if name in _BUILTIN_CHANNEL_CLASSES:
        return
    if not isinstance(cls, type) or not issubclass(cls, Channel):
        raise TypeError(
            f"register_channel: {cls!r} is not a Channel subclass"
        )
    _PLUGIN_CHANNEL_CLASSES[name] = cls


def all_channel_classes() -> dict[str, type[Channel]]:
    """Built-in + plugin 合并的 channel 注册表. Plugin 不会 override
    built-in 同名项."""
    _load_plugin_entry_points()
    out = dict(_BUILTIN_CHANNEL_CLASSES)
    for name, cls in _PLUGIN_CHANNEL_CLASSES.items():
        out.setdefault(name, cls)
    return out


class _ChannelClassesProxy:
    """dict-like proxy 让 ``CHANNEL_CLASSES[name]`` 和 ``in`` 仍然能用,
    背后调 :func:`all_channel_classes` 拿合并后的注册表.

    保留 ``CHANNEL_CLASSES`` 这个 module-level 名字给所有现有 caller —
    它们看到的还是 dict-like 接口, 但 plugin 注册的 platform 自动出现.
    """
    def __getitem__(self, key: str) -> type[Channel]:
        return all_channel_classes()[key]

    def get(self, key: str, default=None):
        return all_channel_classes().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in all_channel_classes()

    def __iter__(self):
        return iter(all_channel_classes())

    def __len__(self) -> int:
        return len(all_channel_classes())

    def keys(self):
        return all_channel_classes().keys()

    def values(self):
        return all_channel_classes().values()

    def items(self):
        return all_channel_classes().items()


CHANNEL_CLASSES = _ChannelClassesProxy()


def list_status() -> list[dict[str, Any]]:
    """Every account across every channel with its runtime state.

    Row shape::

        {
          "platform": "telegram",
          "account_id": "default",
          "name": "Default",
          "enabled": True,
          "configured": True,
          "implemented": True,
        }
    """
    from openprogram.channels import accounts as _accounts
    out: list[dict[str, Any]] = []
    for channel, _cls in CHANNEL_CLASSES.items():
        for acct in _accounts.list_accounts(channel):
            out.append({
                "platform": channel,
                "account_id": acct.account_id,
                "name": acct.name,
                "enabled": _accounts.is_enabled(channel, acct.account_id),
                "configured": _accounts.is_configured(
                    channel, acct.account_id,
                ),
                "implemented": True,
            })
    return out


def build_channel(channel: str,
                  account_id: str = "default") -> Channel | None:
    cls = CHANNEL_CLASSES.get(channel)
    if cls is None:
        return None
    return cls(account_id=account_id)


# Back-compat shims ---------------------------------------------------------

def list_channels_status() -> list[dict[str, Any]]:
    return list_status()


def list_enabled_platforms() -> list[str]:
    return sorted({
        row["platform"] for row in list_status()
        if row["enabled"] and row["configured"]
    })


__all__ = [
    "Channel",
    "CHANNEL_CLASSES",
    "register_channel",
    "all_channel_classes",
    "list_status",
    "list_channels_status",
    "list_enabled_platforms",
    "build_channel",
]
