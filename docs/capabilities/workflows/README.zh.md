# Agentic Workflows

这一页介绍每个受支持 OpenProgram release 已包含的现成 agent 及其使用方式。如果你想直接用而不是自己写函数，从这里开始。

## 是什么

Agentic Workflow 是用 [Agentic Programming](../agentic-programming/README.md) 写成的成品工作流——代码里叫 **harness** 或 **agentic program**：一个自包含的 git 仓库，里面是一组 `@agentic_function`。release 中固定的版本会注册进 OpenProgram，像内置函数一样出现在聊天、Web UI 的 Programs 页和 `openprogram programs run` 里。

三个第一方 workflow：

| Workflow | Release 状态 | 一句话 |
|---|---|---|
| [GUI Agent](gui-agent.md) | 已包含 | 给一句任务，自主操作桌面（截图 → 识别 → 点击 → 验证循环） |
| [Research Agent](research-agent.md) | 已包含 | 从研究选题到可提交论文，带确定性核查层 |
| [Wiki Agent](wiki-agent.md) | 已包含 | 把会话 / 笔记沉淀成模板化 HTML 知识库 |

## 管理命令

```bash
openprogram programs list          # 所有已注册的函数与 program
openprogram programs available     # 第一方状态 + 已装第三方 harness
openprogram programs install <owner>/<repo>   # 任意第三方 harness（git URL 亦可）
openprogram programs install <ref> --upgrade  # 重装 / 升级
openprogram programs uninstall <Harness-Name> # 删除第三方 harness
openprogram programs run <name> -a key=value  # 直接运行一个 program
```

`programs run` 还接受 `--provider`（claude-code / openai-codex / gemini-cli / anthropic / openai / gemini，默认自动探测）和 `--model` 覆盖模型。

第一方 Programs 是 immutable 产品组件。在可变扩展或开发环境中，`programs install` 会克隆额外第三方 harness、安装其声明的依赖并登记批准的来源。

## 用哪种方式触发

- **聊天里**：入口函数以 `as_tool=True` 注册为工具，直接用自然语言描述任务，模型会调用它（如 `gui_agent`、`research_agent`、`wiki_agent`）。
- **命令行**：`openprogram programs run gui_agent -a task="Open Firefox"`。
- **Python 里**：harness 的函数就是普通可 import 的 Python 函数。

## 编写你自己的

任何满足目录契约（`<package>/agentics/__init__.py` 暴露 `AGENTIC_FUNCTIONS`）的仓库都能被同一条 `programs install` 命令安装。契约、最小模板和发布流程见[安装与编写 Harness](../installing-harnesses.md)。

Harness 合同与单个自编程 Workflow package 不同。如果要编写并静态校验供 `create_workflow`、`revise_workflow` 和 `auto_workflow` 使用的版本化 package，请阅读[编写 Workflow package](authoring.md)。
