# Agentic Workflows

这一页告诉你 OpenProgram 自带哪些现成的 agent、怎么装、怎么管理。如果你想直接用而不是自己写函数，从这里开始。

## 是什么

Agentic Workflow 是用 [Agentic Programming](../agentic-programming/README.md) 写成的成品工作流——代码里叫 **harness** 或 **agentic program**：一个自包含的 git 仓库，里面是一组 `@agentic_function`。安装后其函数注册进 OpenProgram，像内置函数一样出现在聊天、Web UI 的 Functions 页和 `openprogram programs run` 里。

三个第一方 workflow：

| Workflow | 安装名 | 一句话 |
|---|---|---|
| [GUI Agent](gui-agent.md) | `gui` | 给一句任务，自主操作桌面（截图 → 识别 → 点击 → 验证循环） |
| [Research Agent](research-agent.md) | `research` | 从研究选题到可提交论文，带确定性核查层 |
| [Wiki Agent](wiki-agent.md) | `wiki` | 把会话 / 笔记沉淀成模板化 HTML 知识库 |

## 管理命令

```bash
openprogram programs list          # 所有已注册的函数与 program
openprogram programs available     # 可安装项 + 已装第三方 harness 的状态
openprogram programs install gui   # gui | research | wiki | all
openprogram programs install <owner>/<repo>   # 任意第三方 harness（git URL 亦可）
openprogram programs install <ref> --upgrade  # 重装 / 升级
openprogram programs uninstall research       # 卸载
openprogram programs run <name> -a key=value  # 直接运行一个 program
```

`programs run` 还接受 `--provider`（claude-code / openai-codex / gemini-cli / anthropic / openai / gemini，默认自动探测）和 `--model` 覆盖模型。

## 用哪种方式触发

- **聊天里**：入口函数以 `as_tool=True` 注册为工具，直接用自然语言描述任务，模型会调用它（如 `gui_agent`、`research_agent`、`wiki_agent`）。
- **命令行**：`openprogram programs run gui_agent -a task="Open Firefox"`。
- **Python 里**：harness 的函数就是普通可 import 的 Python 函数。

## 编写你自己的

任何满足目录契约（`<package>/agentics/__init__.py` 暴露 `AGENTIC_FUNCTIONS`）的仓库都能被同一条 `programs install` 命令安装。契约、最小模板和发布流程见[安装与编写 Harness](../installing-harnesses.md)。
