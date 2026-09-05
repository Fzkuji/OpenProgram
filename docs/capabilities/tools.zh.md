# 内置工具

工具审批行为及运行中切换请参见[工具权限模式](permissions.zh.md)。

OpenProgram 自带一批注册为工具的函数，模型在聊天里直接调用。这一页按 `openprogram/programs/tools/` 目录逐个列出：每个工具做什么、需要什么 key 或本地依赖。大多数工具零配置；需要 key 的集中在网络检索和图像两类。

## 文件与代码

| 工具 | 做什么 | 需要什么 |
|---|---|---|
| `read` | 读文件内容 | 无 |
| `write` | 新建或整体覆盖文件 | 无 |
| `edit` | 文件内字符串替换 | 无 |
| `apply_patch` | Codex / OpenClaw 格式的多文件结构化 patch | 无 |
| `list` | 列目录内容 | 无 |
| `glob` | 按文件名模式找文件 | 无 |
| `grep` | 内容搜索，优先 ripgrep，缺 rg 回退 Python re | 无（装 `rg` 更快） |
| `semble_search` / `semble_find_related` | 语义 + 词法代码搜索，返回排好序的代码块 | 每个受支持的 release 已包含；source developer 通过锁定的项目环境安装 `search` extra |
| `lsp_diagnostics` / `lsp_references` / `lsp_definition` | 从 language server 拿类型检查错误、真实调用点、真实定义位置，见[Language server 工具](lsp.md) | Python 装 `pyright`，TypeScript 装 `typescript-language-server`（缺哪个工具就报哪条安装命令） |

## 执行

| 工具 | 做什么 | 需要什么 |
|---|---|---|
| `bash` | 同步执行 shell 命令，返回 stdout / stderr / 退出码 | 无 |
| `process` | 管理后台 shell 会话（长跑服务、可轮询输出） | 无 |
| `execute_code` | 在独立子进程里跑 Python 片段 | 无 |

## 网络

| 工具 | 做什么 | 需要什么 |
|---|---|---|
| `web_search` | 关键词 → 相关 URL 列表，多后端可选 | 见下方后端表 |
| `web_fetch` | 拉取 URL 并转成可读文本 | 无（装 `trafilatura` 抽取更干净） |
| `playwright_browser` | Playwright 驱动无头 Chromium（open / navigate 等动作） | 每个受支持的 release 已包含 Playwright Chromium |
| `agent_browser` | 经 npm `agent-browser` CLI 驱动浏览器，snapshot 返回可访问性树 | 开发者增加的替代 backend，不用于补齐产品 Browser 功能 |

`web_search` 后端与 key（DuckDuckGo 和 arXiv 免 key，开箱即用）：

| 后端 | 环境变量 |
|---|---|
| DuckDuckGo / arXiv | 无 |
| Brave | `BRAVE_API_KEY` |
| Exa | `EXA_API_KEY` |
| Firecrawl | `FIRECRAWL_API_KEY` |
| Google PSE | `GOOGLE_PSE_API_KEY` + `GOOGLE_PSE_CX` |
| Jina | `JINA_API_KEY` |
| Kagi | `KAGI_API_KEY` |
| MiniMax | `MINIMAX_CODE_PLAN_KEY`、`MINIMAX_CODING_API_KEY` 或 `MINIMAX_API_KEY` |
| Moonshot (Kimi) | `KIMI_API_KEY` 或 `MOONSHOT_API_KEY` |
| Perplexity | `PERPLEXITY_API_KEY` |
| SearXNG | `SEARXNG_URL`（自托管实例地址） |
| Serper | `SERPER_API_KEY` |
| Tavily | `TAVILY_API_KEY` |
| You.com | `YDC_API_KEY` 或 `YOU_API_KEY` |
| Ollama | 本地 Ollama（需 `ollama signin`），或用 `OLLAMA_API_KEY` 走 Ollama Cloud |

## 图像与 PDF

| 工具 | 做什么 | 需要什么 |
|---|---|---|
| `image_generate` | prompt → PNG 存盘 | 任一后端：OpenAI（`OPENAI_API_KEY`）、Gemini（`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`）、fal（`FAL_KEY`） |
| `image_analyze` | 描述图片 / 回答关于图片的问题（本地路径或 URL） | 任一视觉模型 key：OpenAI / Anthropic / Gemini（复用已配置的 provider key） |
| `pdf` | 从 PDF 抽取文本，支持 offset / limit 翻页 | 完整 release 已内置（`pypdf`） |

## 会话与协作

协作分四个域，一词一义，见
[agent 协作](../reference/design/runtime/agent-collaboration.zh.md) §1。

| 域 | 工具 | 做什么 | 需要什么 |
|---|---|---|---|
| 计划 | `todo_create` / `todo_update` / `todo_list` | 会话规划清单：手写的计划清单（建条目、设状态/负责人/依赖、按状态分组列出）。写一条不会启动任何东西 | 无 |
| 执行 | `list_jobs` / `job_output` / `job_stop` | 真正在运行的任务：列出本会话的后台任务、等某个任务的结果、停掉某个任务。只有派活方会话能取结果或取消 | 无 |
| 实体 | `agent` | 新建一个 agent 并取回回复，或用 `to=` 给已存在的 agent 派受管任务。`run_in_background=true` 不阻塞、直接返回 job_id；`start_from` 决定新 agent 从哪起（`clean` / `inherit` / `SID:MSG_ID`）；`archive_when_done=true` 让它在任务结束、结果回流之后自动归档 | 无 |
| 实体 | `list_agents` | agent 列表：有哪些 agent、它们的名字、地址、体量和忙闲（`scope="archived"` 看已归档的） | 无 |
| 实体 | `archive_agent` | 把活干完的 agent 归档：它从 `list_agents` 消失，并拒收后续 `send_message` / `agent(to=)` 投递；`read_conversation` 照读它的历史，`agent(start_from="SID:MSG_ID")` 照 fork。归档不中断在跑的工作、不删数据，所以任何会话都能归档任何 agent；归档单向，没有反归档 | 无 |
| 通讯 | `send_message` | 跟已存在的 agent 说话，按 `"SID:HEAD"` 或名字寻址。不产生任务、不产生 job_id、没有东西可取消，所以任何 agent 都能发 | 无 |
| 通讯 | `read_conversation` | 把任意 agent 的历史读成纯文本（含工具调用），可指定轮次范围和字数预算 | 无 |

| 工具 | 做什么 | 需要什么 |
|---|---|---|
| `program` | 调用任意已注册的 `@agentic_function` | 无 |
| `mixture_of_agents` | 并行问N个模型再综合;默认从模型注册表选,每个provider取一个 | 模型注册表里至少2个provider |
| `ask_user_question` | 向用户提 1–N 个带选项的问题 | 无 |
| `enter_plan_mode` / `exit_plan_mode` | 进入 / 退出计划模式 | 无 |
| `canvas` | 往 markdown 文件的具名块里增量写入 | 无 |
| `memory_*` | 读取持久记忆工作区——`memory_search`（按语义找）、`memory_grep`（找确切字符串）、`memory_get`（读一个文件、章节或段落）、`memory_browse`（看有什么）、`memory_status`（规模与版本），以及 `memory_update` 用来更正某一处。没有记录对话的工具：那件事在后台完成。每个实例只有一份工作区，所有agent、所有对话（含聊天渠道）共用（见[聊天渠道](../integrations/channels.zh.md#谁能和你的机器人说话)）。 | 无 |
| `worktree_*` | git worktree：`worktree_create`（也可直接从 PR 开 worktree，传 `pr="123"` / `"#123"` / GitHub PR 链接，走 `gh`）/ `merge` / `discard` / `list` / `keep` | git |
| `cron` | 登记周期性 agent 任务 | 无 |
| `list_mcp_resources` / `read_mcp_resource` / `list_mcp_prompts` / `get_mcp_prompt` | 把 MCP 的 resources / prompts 原语暴露给模型（`mcp_meta` 目录） | 已配置的 MCP server（见 [MCP](mcp.md)） |
| `tool_search` | 按需加载被延迟的工具的完整 schema——冷门工具在清单里只占一行，模型要用时再取 | 无 |
