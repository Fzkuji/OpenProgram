"""
OpenProgram — Agentic Programming 理念的产品化实现。

在函数里无缝衔接 LLM 调用。用 `@agentic_function` 装饰一个普通 Python 函数，
函数体里的 docstring 就成了给模型的指令；`runtime.exec(...)` 负责把对话带上
调用历史一起发给模型。上下文树（Context）是副产物，自动积累。

双模式：
  - 初学者：跑我们打包好的应用（CLI / Web UI），零代码。
  - 深度用户：`from openprogram import agentic_function` 自己写。

顶层 re-export：
    agentic_function    装饰器（Agentic Programming 的入口符号）
    Runtime             LLM 调用的运行时基类
    Context             执行上下文树
    ask_user            在函数里向用户提问
    新建 / 编辑 / 改进函数走 skill ``agentic-program``，由 agent 直接
    用 Read / Write / Edit 工具操作 .py 文件，不再有专门的 meta 函数。
"""

from openprogram.agentic_programming import (
    Context,
    agentic_function, traced, auto_trace_module, auto_trace_package,
    Runtime,
)
from openprogram.providers.registry import detect_provider, create_runtime, check_providers
from openprogram.programs.functions.buildin.ask_user import (
    ask_user, set_ask_user, FollowUp, run_with_follow_up,
)
from openprogram.programs.functions.buildin.general_action import general_action
from openprogram.programs.functions.buildin.agent_loop import agent_loop
from openprogram.programs.functions.buildin.wait import wait
from openprogram.programs.functions.buildin.deep_work import deep_work
from openprogram.programs.functions.buildin.init_research import init_research

__all__ = [
    "agentic_function",
    "traced",
    "auto_trace_module",
    "auto_trace_package",
    "Runtime",
    "Context",
    "FollowUp",
    "run_with_follow_up",
    "ask_user",
    "detect_provider",
    "create_runtime",
    "check_providers",
    "general_action",
    "agent_loop",
    "wait",
    "deep_work",
    "init_research",
]
