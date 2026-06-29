# 函数设计

函数 / 工具调用框架的内部设计笔记。

**要编写函数？** 面向编写者的文档（使用模式、metadata
规则、三种“选择下一步”的机制、纯 python 辅助工具）
已迁移至用户指南：
[`docs/agentic-programming/`](../../agentic-programming/README.md)。

## 当前来源

| 主题 | 来源 |
|---|---|
| 函数 / 工具调用框架（`@function` / `@agentic_function`、共享注册表、gating、延迟加载） | [`function-calling-unification.md`](function-calling-unification.md) |

## 实现文件

- `openprogram/agentic_programming/function.py`
- `openprogram/agentic_programming/runtime.py`
- `openprogram/agentic_programming/decision.py`
- `openprogram/functions/tools/<name>/`
- `openprogram/functions/agentics/llm_call_example/__init__.py`
