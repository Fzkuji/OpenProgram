# 概览

这一页帮你回答一个问题：OpenProgram 能做什么，以及每种能力去哪页看。能力分三层：编程范式、现成的工作流、扩展机制。

## Agentic Programming 范式

OpenProgram 的底座是 Agentic Programming：**Python 控制流程，LLM 提供推理**。你把任务拆成函数调用图，不需要推理的节点写普通 Python，需要理解 / 生成 / 判断的节点用 `@agentic_function` 装饰，在函数体里通过 `runtime.exec(...)` 调用模型。执行顺序、状态、重试都是普通代码，可以单元测试。

- [Agentic Programming 指南](agentic-programming/README.md) —— 编写函数的学习路径与三种"选择下一步"机制
- [设计哲学](agentic-programming/philosophy.md) —— 范式解决什么问题、为什么反转控制权

## Agentic Workflows：现成的 agent

基于该范式写成的成品工作流（代码里叫 harness / agentic program），装完即用：GUI 自动化、自主科研、个人知识库。用 `openprogram programs list` 查看、`openprogram programs install <name>` 安装，函数注册后既能在聊天里作为工具触发，也能用 `openprogram programs run` 直接跑。

- [Agentic Workflows 总览](workflows/README.md)
- [GUI Agent](workflows/gui-agent.md) —— 给一句任务，自主操作桌面
- [Research Agent](workflows/research-agent.md) —— 从选题到可提交论文
- [Wiki Agent](workflows/wiki-agent.md) —— 把会话沉淀成 HTML 知识库
- [安装与编写 Harness](installing-harnesses.md) —— 第三方 harness 的安装机制与目录契约

## 扩展机制

不写 harness 也能扩展 agent 的能力：

- [Skills](skills.md) —— `SKILL.md` 注册表：给模型按需加载的领域知识与操作手册
- [Plugins](plugins.md) —— 从 pip / npm / git / 本地路径安装插件，向宿主贡献 commands、skills、MCP server 等
- [MCP](mcp.md) —— 接入任意 MCP server，其工具直接出现在聊天里
- [内置工具](tools.md) —— 随框架自带的工具清单（shell、文件、网络检索、图像、PDF 等）及各自需要的 key
