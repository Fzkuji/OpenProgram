# API Reference

> Source: [`openprogram/`](https://github.com/Fzkuji/OpenProgram/tree/main/openprogram/)

## 核心组件

| 组件 | 源文件 | 说明 |
|------|--------|------|
| [`agentic_function`](api/agentic-function.md) | `agentic_programming/function.py` | 装饰器。把普通函数变成 Agentic Function,每次调用记录为 session DAG 的一个节点 |
| [`Runtime`](api/runtime.md) | `agentic_programming/runtime.py` | LLM 运行时。从 DAG 算上下文、调用 LLM、把回复写回 DAG |
| [`create_runtime` 与内置 providers](api/providers.md) | `providers/` | 自动检测或显式创建 Runtime,支持 Anthropic / OpenAI / Gemini / CLI providers |

会话上下文是一张扁平 DAG(节点 = 用户消息 / LLM 调用 / 函数调用),架构见 [`openprogram/context/README.md`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/context/README.md)。

## 编写函数

没有 `create()` / `fix()` 这类 meta 函数——编写、修改、校验 `@agentic_function` 直接用普通文件编辑工具完成,遵循 [`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md)。该 skill 是完整规范:文件布局、装饰器元数据、docstring 与 `content` 的分工、校验清单、冒烟测试。

## 导入

```python
from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.registry import create_runtime
```

只有 `agentic_function` 是 `openprogram` 顶层再导出的符号；`Runtime` 和 `create_runtime` 等都需要按完整路径导入。

## 快速示例

```python
from openprogram import agentic_function
from openprogram.providers.registry import create_runtime

@agentic_function
def observe(task: str, runtime) -> str:
    """Report the UI element on screen that matches a task."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Find the UI element for: {task}. Reply with its label only."
        )},
    ])

rt = create_runtime()
print(observe(task="login button", runtime=rt))
rt.close()
```
