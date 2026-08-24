# 相关项目

把 agent 写成普通的带类型 Python——**docstring 就是 prompt**，**函数签名就是契约**——是好几组人各自独立走到的想法。我们认为这种汇合本身就是方向正确的最强证据，而这些设计之间的差异才是真正有意思的问题。

| 项目 | 共同直觉 | 各自走的路 |
|---|---|---|
| [**NVIDIA NOOA**](https://github.com/NVIDIA-NeMo/labs-OO-Agents) (Apache-2.0) | Agent 是 Python 对象；函数体为 `...` 的方法由 LLM 实现，docstring 是 prompt，类型标注是契约。 | 面向对象：状态在 `self` 上，模型**往 Jupyter 风格的 REPL 里写 Python**（CodeAct）来行动。OpenProgram 把函数留在模块级，让模型**在已注册函数里选择**而不是生成代码——动作空间更窄，也更容易沙箱和回放。 |
| [**DSPy**](https://github.com/stanfordnlp/dspy) (MIT) | 用带类型的 **Signature** 代替手写 prompt，由框架编译。 | 按指标优化 prompt 本身。我们把 prompt 保持固定、可读，把力气花在执行结构上——DAG、重试、上下文范围。两者互补。 |
| [**Marvin**](https://github.com/PrefectHQ/marvin) (Apache-2.0) · [**Mirascope**](https://github.com/Mirascope/mirascope) (MIT) | 装饰一个 Python 函数，用 docstring 和返回类型驱动一次结构化 LLM 调用。 | 聚焦单次类型良好的调用。OpenProgram 补上**跨多次调用**发生的事：共享执行 DAG、`spawn`、fork，以及每次调用的上下文预算。 |
| [**LangGraph**](https://github.com/langchain-ai/langgraph) (MIT) | Agent 运行应是带检查点的可检查图，而不是不透明循环。 | 图事先声明为节点和边。我们的图是**从调用栈记录下来的**——你写普通 Python，DAG 就是实际跑过的轨迹。另见 [OpenProgram 与 LangGraph、AutoGen、CrewAI](ai-agent-frameworks.md)。 |
| [**smolagents**](https://github.com/huggingface/smolagents) (Apache-2.0) | 让模型通过代码行动，而不是死板的 tool JSON。 | 沙箱里写代码的 agent，类似 NOOA。我们接受同一前提——「代码是行动语言」——但在**编写时**用 `@agentic_function` 绑定，这样确定性部分在运行前就可审查。 |
| [**Scriptorium**](https://github.com/Fzkuji/Scriptorium) | 可读的 Agent 记忆；Markdown 笔记；事实回链到来源消息；为 Claude Code 提供 MCP。 | 模型把记忆写成普通文件，所以你可以打开、diff，并把每条事实追回到它来自的那条消息。 |

如果你在这个方向上做事，而我们写错了你的项目——或漏掉了它——请开 PR 或 issue。我们乐意被纠正。

## 致谢

OpenProgram 站在前人的肩膀上。工具框架、provider 抽象和若干工具实现移植或改编自下列项目——各自遵循其原许可证。非常感谢这些作者。

- [**OpenClaw**](https://github.com/openclaw/openclaw)（MIT）——工具注册表的布局
  （`name / description / parameters / execute`）、带 `check_fn` + `requires_env`
  门禁的 provider 抽象、`TOOLSETS` 预设、经 SKILL.md frontmatter + 延迟绑定 `read`
  的 skill 加载。完整克隆放在 `references/openclaw/`（已 gitignore）供浏览。
- [**hermes-agent**](https://github.com/himanshuishere/hermes-agent)
  （MIT）—— `execute_code` 的起点（我们裁掉了 Docker / Modal 层）、
  `mixture_of_agents`，以及多 provider 的 `web_search` / `image_generate` /
  `image_analyze` 工具的整体形态。
- [**pi-coding-agent**](https://github.com/mariozechner/pi-coding-agent)
  （MIT）——经 OpenClaw 引入的规范 AgentSkill 形态
  （`<available_skills>` XML 格式器，name / description / location）。
- [**Claude Code**](https://www.anthropic.com/claude-code) —— `DEFAULT_TOOLS`
  集合的整体人机工学（bash + read / write / edit + glob / grep / list
  + apply_patch + todo 规划板）以及 todo 工具的 JSON schema。
- **Anthropic / OpenAI / Google SDK** —— 线上契约，以及第一方 provider
  流式调用所经过的客户端。三者都作为基础依赖随附；CLI 与 OAuth
  provider 则直接走裸 HTTP。

血缘更具体的工具文件在文件级 docstring 里各自注明了直接灵感来源。这些 MIT
许可的组件保留其原 MIT 条款；组合作品整体以 AGPL-3.0 分发。

## 贡献

这是一个**范式提案**，附带参考实现。欢迎讨论、其他语言的替代实现、验证或挑战此方法的用例，以及 bug 报告。

环境、测试和 pull request 约定见
[CONTRIBUTING.md](https://github.com/Fzkuji/OpenProgram/blob/main/.github/CONTRIBUTING.md)。
