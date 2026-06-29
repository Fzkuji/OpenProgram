# 文档

本目录是 OpenProgram 的文档入口。

## 从这里开始

| 文件 | 用途 |
|---|---|
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | 安装、provider 配置以及可运行的示例 |
| [`features.md`](features.md) | 对 README 所概述的核心功能的详细介绍 |
| [`install.md`](install.md) | 每个 pip extra 各自引入的内容 + 安装后步骤 |
| [`installing-harnesses.md`](installing-harnesses.md) | Harness：用一条命令安装任意（一方/第三方）harness，或编写你自己的 |
| [`troubleshooting.md`](troubleshooting.md) | “它跑不起来”手册（没有 provider、端口被占用、多仓库安装……） |
| [`API.md`](API.md) | 公开 API 索引 |
| [`README_CN.md`](README_CN.md) | 中文项目概览 |
| [`philosophy/agentic-programming.md`](philosophy/agentic-programming.md) | Agentic Programming 的设计理念 |

## Agentic programming 指南

OpenProgram 自有的编程模型——通用 LLM 框架教程不会教你的那些概念。**在编写函数之前请先阅读本节。**

| 文件 | 用途 |
|---|---|
| [`agentic-programming/README.md`](agentic-programming/README.md) | 指南索引 + 学习路径 |
| [`agentic-programming/writing-functions/agentic-function.md`](agentic-programming/writing-functions/agentic-function.md) | `@agentic_function` 使用模式 |
| [`agentic-programming/writing-functions/function-metadata.md`](agentic-programming/writing-functions/function-metadata.md) | 函数元数据——唯一可信来源 |
| [`agentic-programming/choosing-the-next-step/fixed-order-calls.md`](agentic-programming/choosing-the-next-step/fixed-order-calls.md) | 固定顺序的子函数流水线 |
| [`agentic-programming/choosing-the-next-step/tool-calling.md`](agentic-programming/choosing-the-next-step/tool-calling.md) | provider 原生的 tool-use 循环 |
| [`agentic-programming/choosing-the-next-step/next-step-decision.md`](agentic-programming/choosing-the-next-step/next-step-decision.md) | `decision.make` / `exec(choices=)`——由 LLM 选择下一步 |
| [`agentic-programming/writing-functions/pure-python.md`](agentic-programming/writing-functions/pure-python.md) | 纯 Python 辅助函数（不涉及 LLM） |

## API 参考

| 文件 | 用途 |
|---|---|
| [`api/agentic_function.md`](api/agentic-function.md) | `@agentic_function` 装饰器 API |
| [`api/runtime.md`](api/runtime.md) | `Runtime.exec()` 及运行时行为 |
| [`api/providers.md`](api/providers.md) | Provider/运行时类及其配置 |
| [`provider-token-tracking.md`](provider-token-tracking.md) | provider 用量计费语义 |

## 集成指南

| 文件 | 用途 |
|---|---|
| [`INTEGRATION_CLAUDE_CODE.md`](INTEGRATION_CLAUDE_CODE.md) | Claude Code 订阅/运行时集成 |
| [`INTEGRATION_OPENCLAW.md`](INTEGRATION_OPENCLAW.md) | OpenClaw 集成模式 |

## 设计笔记

请以 [`design/README.md`](design/README.md) 作为入口。它将当前规范与归档的审查记录、独立 demo 区分开来。

## 维护规则

- 把当前的 API 事实放在 `api/` 下；把设计理由放在 `design/` 下；
  把函数编写指南放在 `agentic-programming/` 下。
- 优先链接到源文档，而不是在多个文件中重复同样的规则。
- 如果某份设计笔记不再描述当前行为，就把它移到
  `design/archive/`。
- 移动文件后，用相对链接检查来校验文档。
