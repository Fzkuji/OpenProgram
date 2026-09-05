# Built-in tools

See [tool permission modes and live changes](permissions.md) for approval behavior and changes during a task.

OpenProgram ships a set of functions registered as tools that the model calls directly in chat. This page lists them one by one, following the `openprogram/programs/tools/` directory: what each tool does and which keys or local dependencies it needs. Most tools require zero configuration; the ones that need keys cluster in web search and images.

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
| `semble_search` / `semble_find_related` | Semantic + lexical code search returning ranked code blocks | Included in every supported release; source developers install the `search` extra through the locked project environment |
| `lsp_diagnostics` / `lsp_references` / `lsp_definition` | Type-checker errors, real call sites, and true definition sites from a language server — see [Language server tools](lsp.md) | `pyright` for Python, `typescript-language-server` for TypeScript (the tools name the install command when one is missing) |

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
| `playwright_browser` | Playwright-driven headless Chromium (open / navigate and other actions) | Playwright Chromium is included in every supported release |
| `agent_browser` | Drive a browser through the npm `agent-browser` CLI; snapshot returns the accessibility tree | Developer-added alternative backend; not required for product browser functionality |

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
| `pdf` | Extract text from a PDF, with offset / limit paging | Bundled with the complete release (`pypdf`) |

## Session and collaboration

Collaboration splits into four domains, one word each — see
[agent collaboration](../reference/design/runtime/agent-collaboration.md) §1.

| Domain | Tool | What it does | Requires |
|---|---|---|---|
| Planning | `todo_create` / `todo_update` / `todo_list` | The session planning board — a written checklist of intent (create entries, set status / owner / dependencies, list them grouped by status). Writing an entry starts nothing | Nothing |
| Execution | `list_jobs` / `job_output` / `job_stop` | The work actually running: list this session's background tasks, wait for one's result, or stop one. Only the session that dispatched a task may fetch or stop it | Nothing |
| Entity | `agent` | Spawn a new agent and collect its reply, or with `to=` hand a tracked task to an agent that already exists. `run_in_background=true` returns a task id instead of blocking; `start_from` picks where a new agent begins (`clean` / `inherit` / `SID:MSG_ID`); `archive_when_done=true` archives it once its task ends and the result has come back | Nothing |
| Entity | `list_agents` | The agent list: which agents exist, their names, addresses, sizes and busy state (`scope="archived"` shows the archived ones) | Nothing |
| Entity | `archive_agent` | Archive an agent whose work is finished: it leaves `list_agents` and refuses further `send_message` / `agent(to=)` deliveries, while `read_conversation` still reads its history and `agent(start_from="SID:MSG_ID")` still forks it. Any session may archive any agent, since archiving interrupts nothing and deletes nothing; it is one-way, and there is no unarchive | Nothing |
| Communication | `send_message` | Say something to an existing agent, addressed by `"SID:HEAD"` or by name. No task, no task id, nothing to cancel, which is why anyone may write to anyone | Nothing |
| Communication | `read_conversation` | Read any agent's history as a plain-text transcript, tool calls included, with turn ranges and a character budget | Nothing |

| Tool | What it does | Requires |
|---|---|---|
| `program` | Invoke any registered `@agentic_function` | Nothing |
| `mixture_of_agents` | Ask N models in parallel, then synthesize; defaults picked from the model registry, one per provider | At least 2 providers in the model registry |
| `ask_user_question` | Ask the user 1-N questions with options | Nothing |
| `enter_plan_mode` / `exit_plan_mode` | Enter / exit plan mode | Nothing |
| `canvas` | Incrementally write into named blocks of a markdown file | Nothing |
| `memory_*` | Read the persistent memory workspace — `memory_search` (by meaning), `memory_grep` (exact string), `memory_get` (one file, section or block), `memory_browse` (what exists), `memory_status` (size and revision), and `memory_update` to correct one thing. Recording the conversation is not among them: that happens in the background. There is one workspace per instance, shared by every agent and every conversation including chat channels ([Chat Channels](../integrations/channels.md#who-can-talk-to-your-bot)). | Nothing |
| `worktree_*` | Git worktrees: `worktree_create` (also opens a worktree straight from a PR — `pr="123"` / `"#123"` / a GitHub PR URL, via `gh`) / `merge` / `discard` / `list` / `keep` | git |
| `cron` | Register recurring agent tasks | Nothing |
| `list_mcp_resources` / `read_mcp_resource` / `list_mcp_prompts` / `get_mcp_prompt` | Expose MCP resources / prompts primitives to the model (the `mcp_meta` directory) | A configured MCP server (see [MCP](mcp.md)) |
| `tool_search` | Load a deferred tool's full schema on demand — rarely-used tools sit in the listing as one line until the model asks for them | Nothing |
