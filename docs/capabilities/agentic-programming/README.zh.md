# 指南

本目录是 OpenProgram **自有编程模型**的主场——这些概念你在通用的 LLM
框架教程里找不到，在此集中呈现。如果你要为 OpenProgram 编写函数（或正在
权衡是否要写），请先读这里；通用的项目文档（安装、API 索引、故障排查）
位于其他 Tab。范式的来龙去脉见[理念](philosophy.md)。

## 学习路径

按顺序阅读；每一步都建立在前一步之上。

| # | 文档 | 讲什么 |
|---|---|---|
| 1 | [`philosophy.md`](philosophy.md) | 为什么是“agentic programming”——该模型背后的设计理据 |
| 2 | [`writing-functions/agentic-function.md`](writing-functions/agentic-function.md) | `@agentic_function`：封装一个 Python 函数，其函数体通过 `runtime.exec()` 发起 LLM 调用；组合模式 |
| 3 | [`writing-functions/function-metadata.md`](writing-functions/function-metadata.md) | 参数描述、占位符、隐藏参数、`render_range`——函数元数据的唯一可信来源 |
| 4 | [`writing-functions/pure-python.md`](writing-functions/pure-python.md) | 何时**不要**用该装饰器：纯确定性的辅助函数 |

## 选择下一步

OpenProgram 提供三种方式来决定函数内部“接下来执行什么”。它们不是学一次就
扔掉的互斥选项——针对每个任务挑对其中一种，才是核心技能：

| 文档 | 机制 | 何时使用 |
|---|---|---|
| [`choosing-the-next-step/fixed-order-calls.md`](choosing-the-next-step/fixed-order-calls.md) | Python 代码按固定顺序调用子函数 | 步骤顺序事先已知（流水线：draft → review → revise） |
| [`choosing-the-next-step/tool-calling.md`](choosing-the-next-step/tool-calling.md) | 厂商原生 tool use：模型每一轮挑一个函数，循环直到它以文本作答 | 开放式工作，由模型决定调用多少次、调用哪些 |
| [`choosing-the-next-step/next-step-decision.md`](choosing-the-next-step/next-step-decision.md) | `decision.make(prompt, options)` / `runtime.exec(..., choices=...)`：一份文本选项菜单，所选项本身解析为下一个结果 | 路由 / 有限分支；选项可以是普通值，不必是函数；无需厂商 tool-use 支持 |

## 参考

- [`../../reference/api/agentic-function.md`](../../reference/api/agentic-function.md) —— 装饰器 API 速查
- [`../../reference/api/runtime.md`](../../reference/api/runtime.md) —— `Runtime.exec()` 的参数与行为
- [`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md) —— agent 用于编写/校验函数的仓库内 skill（文件布局、规则清单、冒烟测试）
- [`../../reference/design/function/function-calling-unification.md`](../../reference/design/function/function-calling-unification.md) —— 函数调用框架的内部设计笔记（演进 / 重构计划，编写函数时无需阅读）
- *LLM-as-Code: Agentic Programming for Agent Harness*（[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)）—— 描述该范式的论文，已被 KDD 2026 AgenticSE workshop 接收
