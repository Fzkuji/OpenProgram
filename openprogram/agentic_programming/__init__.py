"""
openprogram.agentic_programming — 核心引擎，Agentic Programming 的哲学主体。

这里只放范式的三件套 + 支撑设施：

范式原语（必需）：
    1. @agentic_function  —— 让一个 Python 函数具备"调 LLM"的能力
    2. Runtime            —— LLM 调用的运行时基类
    3. Context            —— 调用发生后自动累积的上下文树

支撑设施（不是范式，是工程性服务）：
    - events.py      —— Context 树事件广播（给 WebUI 实时流式用）
    - persistence.py —— Context 序列化 / 崩溃恢复

不在这里：
    - ask_user / FollowUp / run_with_follow_up
      → 搬到 openprogram.programs.functions.buildin.ask_user
      它们是内置工具函数（和 agent_loop / deep_work 同类），不是范式原语。

零下游依赖：providers / programs / webui 都只能依赖 agentic_programming，不能反向。
"""

from openprogram.agentic_programming.context import (
    Context, on_event, off_event,
)
from openprogram.agentic_programming.function import (
    agentic_function, traced, auto_trace_module, auto_trace_package,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.agentic_programming.dispatch import make_choice
from openprogram.agentic_programming.session import Session
from openprogram.agentic_programming.skills import (
    Skill, load_skills, format_skills_for_prompt, default_skill_dirs,
)

__all__ = [
    "agentic_function",
    "traced",
    "auto_trace_module",
    "auto_trace_package",
    "make_choice",
    "Runtime",
    "Context",
    "Session",
    "on_event",
    "off_event",
    "Skill",
    "load_skills",
    "format_skills_for_prompt",
    "default_skill_dirs",
]
