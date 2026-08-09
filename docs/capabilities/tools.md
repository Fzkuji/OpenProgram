# Built-in tools

OpenProgram ships a set of functions registered as tools that the model calls directly in chat. This page lists them one by one, following the `openprogram/functions/tools/` directory: what each tool does and which keys or local dependencies it needs. Most tools require zero configuration; the ones that need keys cluster in web search and images.

## Files and code

| Tool | What it does | Requires |
|---|---|---|
| `read` | Read file contents | Nothing |
| `write` | Create a file or overwrite one wholesale | Nothing |
| `edit` | String replacement inside a file | Nothing |
| `apply_patch` | Structured multi-file patches in Codex / OpenClaw format | Nothing |
| `list` | List directory contents | Nothing |
| `glob` | Find files by filename pattern | Nothing |
| `grep` | Content search; prefers ripgrep, falls back to Python re without rg | Nothing (`rg` makes it faster) |
| `semble_search` / `semble_find_related` | Semantic + lexical code search returning ranked code blocks | `pip install 'openprogram[search]'` (opt-in extra; the tools answer with this hint when it is missing) |

## Execution

| Tool | What it does | Requires |
|---|---|---|
| `bash` | Run a shell command synchronously, returning stdout / stderr / exit code | Nothing |
| `process` | Manage background shell sessions (long-running services, pollable output) | Nothing |
| `execute_code` | Run a Python snippet in an isolated subprocess | Nothing |

## Web

| Tool | What it does | Requires |
|---|---|---|
| `web_search` | Keywords to a list of relevant URLs, multiple backends | See the backend table below |
| `web_fetch` | Fetch a URL and convert it to readable text | Nothing (`trafilatura` gives cleaner extraction) |
| `playwright_browser` | Playwright-driven headless Chromium (open / navigate and other actions) | Playwright + a browser (install via `openprogram browser install`) |
| `agent_browser` | Drive a browser through the npm `agent-browser` CLI; snapshot returns the accessibility tree | npm package `agent-browser` |

`web_search` backends and keys (DuckDuckGo and arXiv are key-free and work out of the box):

| Backend | Environment variable |
|---|---|
| DuckDuckGo / arXiv | None |
| Brave | `BRAVE_API_KEY` |
| Exa | `EXA_API_KEY` |
| Firecrawl | `FIRECRAWL_API_KEY` |
| Google PSE | `GOOGLE_PSE_API_KEY` + `GOOGLE_PSE_CX` |
| Jina | `JINA_API_KEY` |
| Kagi | `KAGI_API_KEY` |
| MiniMax | `MINIMAX_CODE_PLAN_KEY`, `MINIMAX_CODING_API_KEY`, or `MINIMAX_API_KEY` |
| Moonshot (Kimi) | `KIMI_API_KEY` or `MOONSHOT_API_KEY` |
| Perplexity | `PERPLEXITY_API_KEY` |
| SearXNG | `SEARXNG_URL` (address of a self-hosted instance) |
| Serper | `SERPER_API_KEY` |
| Tavily | `TAVILY_API_KEY` |
| You.com | `YDC_API_KEY` or `YOU_API_KEY` |
| Ollama | Local Ollama (signed in via `ollama signin`), or `OLLAMA_API_KEY` for Ollama Cloud |

## Images and PDF

| Tool | What it does | Requires |
|---|---|---|
| `image_generate` | Prompt to a PNG saved on disk | Any one backend: OpenAI (`OPENAI_API_KEY`), Gemini (`GEMINI_API_KEY` or `GOOGLE_API_KEY`), fal (`FAL_KEY`) |
| `image_analyze` | Describe an image / answer questions about it (local path or URL) | Any vision-model key: OpenAI / Anthropic / Gemini (reuses configured provider keys) |
| `pdf` | Extract text from a PDF, with offset / limit paging | `pypdf` |

## Session and collaboration

| Tool | What it does | Requires |
|---|---|---|
| `agent` (+ `list_tasks` / `task_output` / `task_stop`) | Spawn another agent within the same session and collect its reply, or dispatch a tracked task to an existing agent with `to=`; list, fetch or stop its background tasks (only the dispatching session may fetch or stop a task). `archive_when_done=true` retires the spawned agent once its task reaches terminal state, after the result flowed back | Nothing |
| `program` | Invoke any registered `@agentic_function` | Nothing |
| `send_message` (+ `list_agents`) | Cross-branch communication with existing agents | Nothing |
| `archive_agent` | Retire an agent you created: it leaves `list_agents` (`scope="archived"` still lists it) and refuses further `send_message` / `agent(to=)` deliveries, while `read_conversation` still reads its history and `agent(context="SID:MSG_ID")` still forks it. Only the creating session may archive; there is no unarchive | Nothing |
| `mixture_of_agents` | Ask N models in parallel, then synthesize; defaults picked from the model registry, one per provider | At least 2 providers in the model registry |
| `ask_user_question` | Ask the user 1-N questions with options | Nothing |
| `todo_create` / `todo_update` / `todo_list` | Session planning board — a written checklist of intent (create entries, update status/owner, list grouped by status); dispatch actual work with `agent`, track it with `list_tasks` | Nothing |
| `enter_plan_mode` / `exit_plan_mode` | Enter / exit plan mode | Nothing |
| `canvas` | Incrementally write into named blocks of a markdown file | Nothing |
| `memory_*` | Read / write the persistent memory vault — 13 entry points: `memory_note`, `memory_recall`, `memory_reflect`, `memory_get`, `memory_browse`, `memory_lint`, `memory_ingest`, plus wiki maintenance (`backlinks` / `rename` / `relink` / `delete` / `review` / `status`) | Nothing |
| `worktree_*` | Git worktrees: `worktree_create` / `merge` / `discard` / `list` / `keep` | git |
| `cron` | Register recurring agent tasks | Nothing |
| `list_mcp_resources` / `read_mcp_resource` / `list_mcp_prompts` / `get_mcp_prompt` | Expose MCP resources / prompts primitives to the model (the `mcp_meta` directory) | A configured MCP server (see [MCP](mcp.md)) |
