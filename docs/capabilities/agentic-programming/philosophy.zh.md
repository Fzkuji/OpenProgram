# 设计哲学

> OpenProgram 是 Agentic Programming 这一编程范式的产品化实现。
> 这篇文档讲的是范式本身：它解决什么问题、为什么要反转控制权、核心原语是什么。
> 范式的正式表述见论文 *LLM-as-Code: Agentic Programming for Agent Harness*（[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)），已被 KDD 2026 AgenticSE workshop 接收。

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

一个装饰器。被它装饰的函数，docstring 作为描述性上下文随调用传递，函数体里的 `runtime.exec(...)` 触发模型调用。`runtime` 参数自动注入。

```python
from openprogram import agentic_function

@agentic_function
def summarize(text: str, runtime=None) -> str:
    """Summarize a text in one sentence, preserving the core point."""
    return runtime.exec(content=[{"type": "text", "text": (
        f"Summarize in one sentence, preserving the core point:\n\n{text}"
    )}])
```

外部调用者感觉不到差别 —— `summarize(article)` 看起来和任何 Python 函数一样。

### 2. `Runtime`

LLM 调用的运行时抽象。负责：
- 把当前对话历史打包
- 调用底层 provider（Anthropic / OpenAI / Claude Code / ...）
- 把结果写回上下文

`Runtime.exec()` 是唯一的 LLM 入口。所有模型调用都走这里。

### 3. `Context`

执行过程的自动记录。每个用户轮次、每次 LLM 调用、每次函数调用都是同一张**扁平 DAG** 上的一个节点；边有两种：`caller`（哪个函数发起了这个节点）和 `reads`（一次 LLM 调用的 prompt 看到了哪些节点）。每个节点记录输入、输出、token 用量、耗时、失败原因。

这张 DAG 不只是执行轨迹——它同时是**每次 LLM 调用的历史来源**：`runtime.exec()` 从 DAG 渲染出自己的消息历史。装饰器上的两个旋钮按函数塑造这条数据流：

- `expose` —— 一次调用完成后向父级暴露什么（默认 `"io"`：函数名 + 输入 + 输出，内部细节隐藏）。
- `render_range` —— 函数自身的 `exec` 拉取多少历史。`render_range={"callers": 0}` 得到一个隔离的草稿上下文，看不到任何先前对话。

于是上下文管理不再是 prompt 拼接的体力活，而变成写在函数声明上的属性。同一张 DAG 也兼作你的调试视图：可视化、token 记账、回放失败路径。

## 衍生概念

### LLM 也写代码

LLM 不只是运行时的推理引擎，它也可以**写代码**——生成、修改、修复符合规范的 `@agentic_function`。这件事不需要专门的 `create()` / `fix()` 框架函数；agent 直接用普通的文件编辑工具完成，遵循 [`agentic-programming` skill](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md) 这份规范——文件放哪、装饰器元数据、docstring 与 `content` 的分工、校验清单。后台 watcher 会重扫 `functions/agentics/` 并热加载新模块：import 时 `@agentic_function` 装饰器触发、自行注册，刚写完的函数无需重启即可调用。

代码是数据，LLM 是编译器，函数是产品 —— 循环闭合。

### 双模式

Agentic Programming 同时是：
- **一个库** —— 你写 `@agentic_function`，手动搭 pipeline
- **一个跑着的产品** —— 在 CLI 或 WebUI 里聊天，让 agent 帮你把函数写出来；生成的文件落到 `functions/agentics/` 并热加载

初学者从提需求开始，拿到手的就是完整可读的 Python 文件。想深挖的人再 import 手写。这是一个**可以被逐步理解**的工具。

## 和传统 Agent 框架的对照

| 场景 | LangChain / AutoGPT | Agentic Programming |
|------|---------------------|---------------------|
| "抓 10 个页面，每个生成摘要" | Agent 自己决定顺序和并行 | Python 写 `for url in urls: summarize(fetch(url))` |
| "连续 3 次对话里记住上下文" | 把对话塞进 memory store，每次查询 | 就是 Python 函数的局部变量 |
| "让 LLM 决定调哪个工具" | function calling + agent loop | `runtime.exec(tools=[...])` 或 `decision.make(prompt, options)` |
| "错了要重试" | Agent 自己决定 | `try / except` + 代码门控：无效的选择被校验拦下，让模型重新决策 |

不是说 Agent 框架错了，它们适合一类任务（完全开放、目标模糊）。但大多数你想做的事，其实都能用 Agentic Programming 更可靠地完成。

## OpenProgram = 范式的产品化

`agentic_programming/` 子包是范式的引擎代码。`context/` 实现扁平 DAG 上下文模型。`providers/` 适配各家 LLM。`functions/agentics/` 是这个范式下已经写好的函数和应用。`webui/` 让初学者不写代码也能跑。

范式先行，产品为用。

---

延伸阅读：
- [快速开始](../../start/GETTING_STARTED.md)
- [API 参考](../../reference/api/)
- [设计细节](../../reference/design/)
