# Agentic Programming — 设计哲学

> OpenProgram 是 Agentic Programming 这一编程范式的产品化实现。
> 这篇文档讲的是范式本身：它解决什么问题、为什么要反转控制权、核心原语是什么。

## 问题

当前所有 LLM Agent 框架都把控制权交给模型：
- **做什么** 由 LLM 决定（planner 先规划、agent 再执行）
- **何时做** 由 LLM 决定（while loop 直到 agent 说"我做完了"）
- **怎么做** 由 LLM 决定（工具调用、参数、顺序）

代价：
- **执行不可预测** —— 同样的输入，每次轨迹不同
- **上下文爆炸** —— 每一步都把历史塞给模型
- **输出无保证** —— 没人能说"这个任务一定会跑完"
- **调试地狱** —— 出错时，你分不清是 prompt 问题、工具问题、还是模型幻觉

根本原因：**用一个黑箱概率系统，去做一件本来就能用确定性代码完成的工作**。

## 反转：Python 控流，LLM 推理

Agentic Programming 把控制权还给程序员：

| 维度 | 传统 Agent | Agentic Programming |
|------|-----------|---------------------|
| 流程 | LLM 规划 | Python 代码 |
| 决策 | LLM 每步判断 | Python 决定调不调 LLM |
| 状态 | 塞在上下文里 | 函数变量、返回值 |
| 可测 | prompt 回归 | 单元测试 |

把一个复杂任务拆解成函数调用图。图上的每个节点，你决定：
- **不需要推理的** —— 用普通 Python 函数
- **需要理解 / 生成 / 判断的** —— 用 `@agentic_function` 装饰，函数体里调 `runtime.exec(...)` 触发 LLM

LLM 变成一个工具，被你调用、被你约束、被你组合。

## 三个原语

整个范式只有三样东西：

### 1. `@agentic_function`

一个装饰器。被它装饰的函数，docstring 自动变成给 LLM 的指令，`runtime.exec(...)` 触发模型调用。

```python
from openprogram import agentic_function

@agentic_function
def summarize(text: str) -> str:
    """用一句话概括这段内容，保留核心观点。"""
    return runtime.exec(content=[{"type": "text", "text": text}])
```

外部调用者感觉不到差别 —— `summarize(article)` 看起来和任何 Python 函数一样。

### 2. `Runtime`

LLM 调用的运行时抽象。负责：
- 把当前对话历史打包
- 调用底层 provider（Anthropic / OpenAI / Claude Code / ...）
- 把结果写回上下文

`Runtime.exec()` 是唯一的 LLM 入口。所有模型调用都走这里。

### 3. `Context`

函数调用图的自动记录。每次 `@agentic_function` 进入/返回 / 触发 `runtime.exec()`，都往一棵树上挂节点。节点上记：输入、输出、token 用量、耗时、失败原因。

这棵树**不给模型看**（除非你主动把它拼进 prompt）。它给你看 —— 用来调试、做可视化、回放失败路径。

上下文不是卖点，是 LLM 调用的副产物。卖点是"你可以在函数里无缝调模型"。

## 衍生概念

### LLM 也写代码

LLM 不只是运行时的推理引擎，它也可以**写代码**——生成、修改、修复符合规范的 `@agentic_function`。这件事不需要专门的 `create()` / `fix()` 框架函数(它们以前也只是包了一次 LLM 调用加一次文件写入);agent 直接用普通的文件编辑工具完成,遵循 [`agentic-programming` skill](../../skills/agentic-programming/SKILL.md) 这份规范——文件放哪、装饰器元数据、docstring 与 `content` 的分工、校验清单。

代码是数据，LLM 是编译器，函数是产品 —— 循环闭合。

### 双模式

Agentic Programming 同时是：
- **一个库** —— 你写 `@agentic_function`，手动搭 pipeline
- **一个 CLI** —— `openprogram create "任务描述" --name my_fn`，让 LLM 帮你写

初学者从 CLI 开始，生成的代码就是完整可读的 Python 文件。想深挖的人再 import 手写。这是一个**可以被逐步理解**的工具。

## 和传统 Agent 框架的对照

| 场景 | LangChain / AutoGPT | Agentic Programming |
|------|---------------------|---------------------|
| "抓 10 个页面，每个生成摘要" | Agent 自己决定顺序和并行 | Python 写 `for url in urls: summarize(fetch(url))` |
| "连续 3 次对话里记住上下文" | 把对话塞进 memory store，每次查询 | 就是 Python 函数的局部变量 |
| "让 LLM 决定调哪个工具" | function calling + agent loop | 写 `tool = runtime.exec(...); dispatch(tool)` |
| "错了要重试" | Agent 自己决定 | `try / except + retry` |

不是说 Agent 框架错了，它们适合一类任务（完全开放、目标模糊）。但大多数你想做的事，其实都能用 Agentic Programming 更可靠地完成。

## OpenProgram = 范式的产品化

`agentic_programming/` 子包是范式的引擎代码。`providers/` 适配各家 LLM。`programs/` 是这个范式下已经写好的函数和应用。`webui/` 让初学者不写代码也能跑。

范式先行，产品为用。

---

延伸阅读：
- [快速开始](../GETTING_STARTED.md)
- [API 参考](../api/)
- [设计细节](../design/)
