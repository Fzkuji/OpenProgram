# Wiki Agent

个人知识库 agent：把会话、笔记灌进一个模板驱动的 HTML wiki。页面是真实的 HTML 文件，任何浏览器直接打开、可静态托管、可用 git 管版本；agent 只往固定模板的具名 slot 里填内容，从不手写 HTML / CSS。自带全文搜索、文件夹自动索引（每个文件夹的 `README.html` 由当前内容自动生成）和整理式 ingest 流水线。

## 安装

```bash
openprogram programs install wiki
```

依赖极小：Jinja2 + PyYAML。模板 shell 用 Jinja2 + Bootstrap 5（CDN 引入），零构建步骤，不需要 Node。

## 怎么用

入口函数名为 **`wiki_agent`**，聊天里用自然语言描述要对 wiki 做什么即可触发，例如：

- "ingest these notes about transformers"（把上面的对话灌进 wiki）
- "enrich the Methodology landing page"
- "browse the vault"
- "check for broken links"
- "find pages about distillation"

命令行直接运行：

```bash
openprogram programs run wiki_agent -a task="Ingest the notes above into the wiki"
```

函数签名 `wiki_agent(task, vault="", purpose="", audience="", ...)`。它是一个调度器：用 next-step decision（`decision.make`）把任务路由到五种内部操作之一——**ingest / enrich / browse / lint / search**，各分支处理器是调用 `wiki_agent_harness.Wiki` 的普通 Python。`vault`（知识库根目录）、`purpose`、`audience` 是隐藏参数，不出现在聊天工具表单里；`vault` 不传时回落到 runtime 的工作目录。

harness 本身刻意保持松散：它只提供渲染层、slot 原语、全文搜索、文件夹自动索引和 ingest 流水线五样东西，移动 / 删除 / grep / 编辑等其余操作 agent 用普通的 shell 和文件工具完成。slot 是页面里 HTML 注释包围的区域，agent 每次只改一个 slot，多次 ingest 干净累积，不重写整页。

## 依赖注意

- 不依赖 openprogram 也能用：不装 OpenProgram 时，纯 Python 的 `Wiki` 类和 CLI 照常工作，只是没有 `wiki_agent` 这个聊天入口。
- 下游项目（论文调研、记忆库、CRM 等）通过传入自己的模板和 prompt 来特化 ingest 流水线，不需要 fork。

源码与 README：`openprogram/functions/agentics/Wiki-Agent-Harness/`，上游仓库 [Fzkuji/Wiki-Agent-Harness](https://github.com/Fzkuji/Wiki-Agent-Harness)。
