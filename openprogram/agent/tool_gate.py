"""
tool.before 同步问询点（gate）——全框架唯一的拦截位。

观察（EventBus）是异步的，谁也拦不住正在发生的事；这里是同步的：
``agent_loop`` 在每次 ``tool.execute()`` 之前，拿着 ``tool.before`` 事件
来问一圈已注册的 gate。有谁给出 deny 理由，工具就不执行，理由作为
error tool result 回给模型（设计：docs/design/proactive/execution-model.md §2）。

规则：
* gate 必须快——这是同步热路径，不许调 LLM、不许慢 IO。
* 多个 gate 表态取最严：任一 deny 即拦下（理由合并）。
* gate 自身抛异常 → 按 allow 处理（fail-open）并写 stderr；
  "critical fail-closed" 的分级等规则层（policy）进场时再加。
* 对 subagent 同样生效：本问询点位于 permission_mode 的 approval
  包装之外，``permission_mode="bypass"`` 关不掉它。
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

from .event_bus import Event

# gate 函数：拿到 tool.before 事件，返回 None（放行）或 deny 理由字符串。
ToolGate = Callable[[Event], "str | None"]

_gates: list[ToolGate] = []
_lock = threading.Lock()


class ToolGateDenied(Exception):
    """Raised inside the tool-execution try block when a gate denies the
    call — caught by the existing error path, so the model receives the
    deny reason as an error tool result."""


def register_tool_gate(gate: ToolGate) -> Callable[[], None]:
    """Register a gate. Returns an unregister function."""
    with _lock:
        _gates.append(gate)

    def unregister() -> None:
        with _lock:
            try:
                _gates.remove(gate)
            except ValueError:
                pass

    return unregister


def decide_tool_gate(event: Event) -> str | None:
    """Ask every gate; return the merged deny reason, or None to allow."""
    with _lock:
        gates = list(_gates)
    reasons: list[str] = []
    for gate in gates:
        try:
            verdict = gate(event)
        except Exception as exc:
            # fail-open：gate 的 bug 不能砖掉所有工具调用
            print(f"Tool gate error (fail-open): {exc}", file=sys.stderr)
            continue
        if verdict:
            reasons.append(str(verdict))
    if reasons:
        return "; ".join(reasons)
    return None
