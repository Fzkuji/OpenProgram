"""规则出手的动作（Action）。

设计 execution-model.md §2-§3：
* 挡路（gate）规则只返回 Gate（allow / deny / ask）。
* 旁观（observer）规则返回 Notify / Inject / Prepare（或 None 不出手）。

动作只是"决定 + 数据"，怎么落地（拦工具、推前端、注入模型、起后台任务）
由引擎做——规则保持短小、只管判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


class Action:
    """所有动作的基类（仅作类型标记）。"""


# ─── 挡路 lane 专用 ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Gate(Action):
    """挡路规则对一次 tool.before 的裁决。

    verdict:
      * "allow" — 放行（等价于不出手，给规则一个显式表达）
      * "deny"  — 拦死，reason 作为 error tool result 回给模型
      * "ask"   — 问用户，用户同意才放（接 ApprovalRegistry，后续单元接）
    """
    verdict: Literal["allow", "deny", "ask"]
    reason: str = ""

    @staticmethod
    def allow() -> "Gate":
        return Gate("allow")

    @staticmethod
    def deny(reason: str) -> "Gate":
        return Gate("deny", reason)

    @staticmethod
    def ask(reason: str) -> "Gate":
        return Gate("ask", reason)


# ─── 旁观 lane 专用 ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Notify(Action):
    """给用户一个非打扰提醒（不打断 agent）。落地为前端可见的提示。"""
    message: str
    severity: Literal["info", "warn"] = "info"
    ref: str = ""  # 可选：关联的文件/工具，前端可据此跳转


@dataclass(frozen=True)
class Inject(Action):
    """在模型下次思考前悄悄塞一句提示（不打扰用户）。"""
    text: str


@dataclass(frozen=True)
class Prepare(Action):
    """起一个只读后台小任务先做功课（execution-model §3）。

    spec 描述要做什么；引擎用受限（只读）工具集起 task，结果以
    proactive.prepared 事件回流，由升级规则决定要不要 Notify。
    """
    spec: dict = field(default_factory=dict)
