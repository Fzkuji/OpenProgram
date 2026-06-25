"""Policy 基类 + 进程级注册表。

设计 execution-model.md §1：一条规则就是一个 Python 类，框架认四样——
on（盯哪类事件）、lane（挡路 gate / 旁观 observer）、cooldown_s（防刷屏）、
evaluate（来了怎么办）。加一条新规则 = 写个类 + register_policy（§6）。
"""
from __future__ import annotations

import threading
from typing import Literal

from openprogram.agent.event_bus import Event

from .actions import Action

Lane = Literal["gate", "observer"]


class Policy:
    """规则基类。子类覆写类属性 + evaluate。

    约束（execution-model §2）：lane="gate" 的规则只盯 {"tool.before"}，
    evaluate 必须快——不调 LLM、不读网络。lane="observer" 在旁路异步跑，
    可以慢，但只能返回 Notify/Inject/Prepare，不能返回 Gate。
    """

    on: set[str] = set()        # 盯哪类事件
    lane: Lane = "observer"
    cooldown_s: float = 0.0      # 出手后多久内同一情况不再出手
    name: str = ""              # 默认取类名

    def evaluate(self, event: Event, state) -> Action | None:
        """看当前事件 + 当前状态，返回一个 Action 出手，或 None 不管。"""
        raise NotImplementedError

    def dedup_key(self, event: Event, state) -> str:
        """同一"情况"的去重键，配合 cooldown_s 防刷屏。默认按 policy 名 +
        session，子类可覆写得更细（比如带上工具名 / 文件路径）。"""
        return f"{self.name or type(self).__name__}:{event.metadata.get('session', '')}"


# 进程级注册表

_policies: list[Policy] = []
_lock = threading.Lock()


def register_policy(policy: Policy) -> None:
    """注册一条规则。重复注册同名规则会被忽略（幂等，便于 worker 重启）。"""
    if not policy.name:
        policy.name = type(policy).__name__
    with _lock:
        if any(p.name == policy.name for p in _policies):
            return
        _policies.append(policy)


def registered_policies(lane: Lane | None = None) -> list[Policy]:
    """当前注册的规则（可按 lane 过滤）。"""
    with _lock:
        if lane is None:
            return list(_policies)
        return [p for p in _policies if p.lane == lane]


def clear_policies() -> None:
    """清空注册表（测试用）。"""
    with _lock:
        _policies.clear()
