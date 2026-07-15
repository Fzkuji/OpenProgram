# 内置工具

OpenProgram 自带一批注册为工具的函数，模型在聊天里直接调用。这一页按 `openprogram/functions/tools/` 目录逐个列出：每个工具做什么、需要什么 key 或本地依赖。大多数工具零配置；需要 key 的集中在网络检索和图像两类。

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
| `semble_search` / `semble_find_related` | 语义 + 词法代码搜索，返回排好序的代码块 | `semble` Python 库 |

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
| `playwright_browser` | Playwright 驱动无头 Chromium（open / navigate 等动作） | Playwright + 浏览器（`openprogram browser install` 安装） |
| `agent_browser` | 经 npm `agent-browser` CLI 驱动浏览器，snapshot 返回可访问性树 | npm 包 `agent-browser` |

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
| `pdf` | 从 PDF 抽取文本，支持 offset / limit 翻页 | `pypdf` |

## 会话与协作

| 工具 | 做什么 | 需要什么 |
|---|---|---|
| `task`（+ `await_task` / `cancel_task`） | 在同一会话里派生另一个 agent 并取回回复 | 无 |
| `spawn_program` | 调用任意已注册的 `@agentic_function` | 无 |
| `message_branch`（+ `list_branches` / `list_sessions`） | 分支间通信 | 无 |
| `mixture_of_agents` | 并行问 N 个模型再综合 | 已配置的多个 provider key |
| `ask_user_question` | 向用户提 1–N 个带选项的问题 | 无 |
| `todo_read` / `todo_write` | 会话内任务清单 | 无 |
| `enter_plan_mode` / `exit_plan_mode` | 进入 / 退出计划模式 | 无 |
| `canvas` | 往 markdown 文件的具名块里增量写入 | 无 |
| `memory_*` | 持久记忆库读写——13 个入口：`memory_note`、`memory_recall`、`memory_reflect`、`memory_get`、`memory_browse`、`memory_lint`、`memory_ingest`，外加 wiki 维护（`backlinks` / `rename` / `relink` / `delete` / `review` / `status`） | 无 |
| `worktree_*` | git worktree：`worktree_create` / `merge` / `discard` / `list` / `keep` | git |
| `cron` | 登记周期性 agent 任务 | 无 |
| `list_mcp_resources` / `read_mcp_resource` / `list_mcp_prompts` / `get_mcp_prompt` | 把 MCP 的 resources / prompts 原语暴露给模型（`mcp_meta` 目录） | 已配置的 MCP server（见 [MCP](mcp.md)） |
