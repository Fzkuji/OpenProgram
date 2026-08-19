"""
OpenProgram — Agentic Programming 理念的产品化实现。

在函数里无缝衔接 LLM 调用。用 `@agentic_function` 装饰一个普通 Python 函数，
函数体里的 docstring 就成了给模型的指令；`runtime.exec(...)` 负责把对话带上
调用历史一起发给模型。上下文树（Context）是副产物，自动积累。

三种用法：
  - 初学者：跑我们打包好的应用（CLI / Web UI），零代码。
  - 深度用户：`from openprogram import agentic_function` 自己写。
  - 嵌入别的框架：只 import 下面这四个符号，自带 LLM 调用（`Runtime(call=...)`）、
    自选会话目录（`SessionStore(root_path=...)`），不启动 webui/TUI/CLI。

顶层 re-export 是嵌入用的最小入口：``agentic_function`` / ``Runtime`` /
``decision`` / ``Session``。其它符号（``ask_user`` / 各 provider helper 等）
走全路径：

    from openprogram.programs.functions.agentic.ask_user import ask_user
    from openprogram.providers.registry import create_runtime

四个名字都经模块级 ``__getattr__`` 懒加载：``import openprogram`` 本身不拉起
runtime / store / providers，嵌入方按需付导入成本。

新建 / 编辑 / 改进 @agentic_function 由 agent 按 API 文档直接使用文件工具操作
.py 文件，不再有专门的 meta 函数或随包提供的默认 skill。
"""

__all__ = ["agentic_function", "Runtime", "decision", "Session"]

_LAZY = {
    "agentic_function": ("openprogram.agentic_programming.function", "agentic_function"),
    "Runtime": ("openprogram.agentic_programming.runtime", "Runtime"),
    "Session": ("openprogram.agentic_programming.session", "Session"),
    "decision": ("openprogram.agentic_programming.decision", None),
}


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module_path, attr = target
    module = importlib.import_module(module_path)
    value = module if attr is None else getattr(module, attr)
    globals()[name] = value  # cache: later lookups skip __getattr__
    return value


def __dir__():
    return sorted(list(globals()) + __all__)
