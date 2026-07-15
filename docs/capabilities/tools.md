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
| `semble` | Semantic + lexical code search returning ranked code blocks | `semble` Python library |

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
| `browser` | Playwright-driven headless Chromium (open / navigate and other actions) | Playwright + a browser (install via `openprogram browser`) |
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
| MiniMax | `MINIMAX_CODE_PLAN_KEY` |
| Moonshot (Kimi) | `KIMI_API_KEY` |
| Perplexity | `PERPLEXITY_API_KEY` |
| SearXNG | `SEARXNG_URL` (address of a self-hosted instance) |
| Serper | `SERPER_API_KEY` |
| Tavily | `TAVILY_API_KEY` |
| You.com | `YDC_API_KEY` or `YOU_API_KEY` |
| Ollama | Local Ollama, no key |

## Images and PDF

| Tool | What it does | Requires |
|---|---|---|
| `image_generate` | Prompt to a PNG saved on disk | Any one backend: OpenAI (`OPENAI_API_KEY`), Gemini (`GEMINI_API_KEY` or `GOOGLE_API_KEY`), fal (`FAL_KEY`) |
| `image_analyze` | Describe an image / answer questions about it (local path or URL) | Any vision-model key: OpenAI / Anthropic / Gemini (reuses configured provider keys) |
| `pdf` | Extract text from a PDF, with offset / limit paging | `pypdf` |

## Session and collaboration

| Tool | What it does | Requires |
|---|---|---|
| `task` | Spawn another agent within the same session and collect its reply | Nothing |
| `spawn_program` | Invoke any registered `@agentic_function` | Nothing |
| `agent_collab` | Cross-branch communication (`message_branch`) | Nothing |
| `mixture_of_agents` | Ask N models in parallel, then synthesize | Multiple configured provider keys |
| `clarify` | Ask the user 1-N questions with options (AskUserQuestion) | Nothing |
| `todo` | In-session task list (todo_read / todo_write) | Nothing |
| `plan_mode` | Enter / exit plan mode | Nothing |
| `canvas` | Incrementally write into named blocks of a markdown file | Nothing |
| `memory` | Read / write the persistent memory store (record observations, retrieve, etc. — seven entry points) | Nothing |
| `worktree` | Create / merge / discard git worktrees | git |
| `cron` | Register recurring agent tasks | Nothing |
| `mcp_meta` | Expose MCP resources / prompts primitives to the model | A configured MCP server (see [MCP](mcp.md)) |
