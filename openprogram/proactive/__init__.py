"""
Proactive layer — 事件层之上的主动性应用（迁移步 5）。

事件底座（openprogram/agent/event_bus.py 等）已经把框架的全部活动统一成
一条可订阅的事件流。这个包是它的第一个真正消费者：一组**规则（Policy）**
订阅事件，在该出手时出手——挡下危险命令、提醒该补的测试、推模型去验证。

设计：docs/design/proactive/（overview / execution-model / policies-mvp /
invariants）。本包刻意只做"地基级"主动性，不含归档在 _research_archive/ 的
打扰预算 / 熔断 / 回放等研究级装修。

公开面：
* Action — 规则出手的几种动作（Gate / Notify / Inject / Prepare）
* Policy — 规则基类（on / lane / cooldown_s / evaluate）
* register_policy / registered_policies — 注册表
* install_proactive — 把引擎接到事件层（worker 启动调一次）
"""
from __future__ import annotations

from .actions import Action, Gate, Inject, Notify, Prepare
from .policy import Policy, register_policy, registered_policies, clear_policies
from .engine import install_proactive

__all__ = [
    "Action",
    "Gate",
    "Inject",
    "Notify",
    "Prepare",
    "Policy",
    "register_policy",
    "registered_policies",
    "clear_policies",
    "install_proactive",
]
