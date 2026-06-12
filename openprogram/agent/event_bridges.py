"""
B 类系统事件桥：把子系统已有的信号翻译成统一 Event 进总线。

设计（docs/design/proactive/event-layer.md §3）：B 类源不改源头逻辑，
只架单向桥。auth 自带规范的 ``subscribe/_emit``，所以它走真正的"桥"
（订阅→翻译→emit）；context / channels / memory / webui watcher 是
自家代码，直接在源头加 ``emit_safe`` tap，不经过本模块。

worker 启动时调一次 :func:`install_event_bridges`（幂等）。
"""
from __future__ import annotations

import threading

from openprogram.auth.types import AuthEvent, AuthEventType

from .event_bus import Event, get_event_bus, make_event

# AuthEventType → 事件层 type。只桥设计里点名的三种（限流/轮换/耗尽是
# 明确的可响应时机）；登录/刷新/档案生命周期先不进总线，免得变垃圾场
# （边界原则：有消费者想响应才是事件）。
_AUTH_TYPE_MAP = {
    AuthEventType.POOL_MEMBER_COOLDOWN: "credential.cooldown",
    AuthEventType.POOL_ROTATED: "credential.rotated",
    AuthEventType.POOL_EXHAUSTED: "credential.exhausted",
}

_installed = False
_lock = threading.Lock()


def translate_auth_event(ev: AuthEvent) -> Event | None:
    """AuthEvent → 统一 Event；不在桥接清单里的返回 None。"""
    event_type = _AUTH_TYPE_MAP.get(ev.type)
    if event_type is None:
        return None
    return make_event(
        event_type,
        "system",
        payload={
            "provider": ev.provider_id,
            "profile": ev.profile_id,
            "credential": ev.credential_id,
            "detail": dict(ev.detail or {}),
        },
    )


def install_event_bridges(auth_store=None, bus=None) -> bool:
    """把 auth 桥装上（幂等，重复调用是 no-op）。

    ``auth_store`` / ``bus`` 仅测试注入用；正常路径用进程单例。
    返回是否本次真正安装了。
    """
    global _installed
    with _lock:
        if _installed:
            return False
        _installed = True

    if auth_store is None:
        from openprogram.auth.store import get_store
        auth_store = get_store()
    if bus is None:
        bus = get_event_bus()

    def _forward(auth_ev: AuthEvent) -> None:
        try:
            ev = translate_auth_event(auth_ev)
            if ev is not None:
                bus.emit(ev)
        except Exception:
            pass  # 桥的故障不能影响 auth 自身

    auth_store.subscribe(_forward)
    return True
