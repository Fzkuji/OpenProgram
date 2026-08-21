# 函数设计

函数 / 工具调用框架的内部设计笔记。

**要编写函数？** 面向编写者的文档（使用模式、metadata
规则、三种“选择下一步”的机制、纯 python 辅助工具）
已迁移至用户指南：
[`docs/agentic-programming/`](../../../capabilities/agentic-programming/README.md)。

## 当前来源

| 主题 | 来源 |
|---|---|
| 函数 / 工具调用框架（`@function` / `@agentic_function`、共享注册表、gating、延迟加载） | [`calling-unification.md`](calling-unification.md) |
| Agentic program：工具、技能、agentic function统一成一个概念放在同一条谱上，该写哪一种的判据，两个装饰器要不要合并，模型眼里该是几种东西 | [`agentic-program.html`](agentic-program.html) |
| 装饰器用法、元数据规范、tool-call 循环、纯 Python 辅助函数 | 产品页在 [`capabilities/agentic-programming/`](../../../capabilities/agentic-programming/README.md) —— 那里是唯一归属；这里原先的设计副本已删除 |

## 实现文件

- `openprogram/agentic_programming/function.py`
- `openprogram/agentic_programming/runtime.py`
- `openprogram/agentic_programming/decision.py`
- `openprogram/programs/tools/<name>/`
- `openprogram/programs/workflow/text/__init__.py`
