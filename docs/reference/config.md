# Configuration

The keys in `~/.openprogram/config.json`, what `openprogram config` can read and write, and the environment variable roundup. For the everyday entry point to changing settings, see [Configuration and data directory](../server/configuration.md).

## What openprogram config can read and write

```bash
openprogram config list              # every setting: value, group, apply mode
openprogram config get ui.port
openprogram config set ui.web_port 8101
```

The settings registry is defined in `openprogram/config_schema.py` (the single source of truth; the setup wizard, the TUI settings page, and the Web settings page all render from it). Every setting is labeled with an apply mode: `live` takes effect immediately, `next_start` takes effect the next time the worker starts.

| key | Group | Meaning | Default | Applies |
|-----|------|------|------|------|
| `ui.port` | Ports | backend (FastAPI, API + WebSocket) port | 18109 | next start |
| `ui.web_port` | Ports | frontend (Web UI) port | 18100 | next start |
| `ui.open_browser` | Ports | whether `openprogram web` opens the browser automatically | true | next start |
| `search.default_provider` | Search | default web search provider; `auto` picks the highest-priority configured one | auto | live |
| `memory.backend` | Memory | `local` (on-disk memory tools) or `none` (disabled) | local | next start |
| `tools.disabled.<name>` | Tools | per-tool switch; written as members of the `tools.disabled` list | all enabled | live |
| `providers.<name>` | Providers | read-only status row (configured or not); configure with `openprogram providers login` or the Web UI | — | — |

## Top-level keys in config.json

The top-level keys actually written to `~/.openprogram/config.json` (do not edit by hand — go through `openprogram config set`, the setup wizard, or the Web UI):

| Key | Meaning | Code |
|----|------|------|
| `ui` | `{port, web_port, open_browser}`, see the table above | `openprogram/config_schema.py` |
| `search` | `{default_provider}` | `openprogram/setup.py` |
| `tools` | `{disabled: [tool name, ...]}` | `openprogram/setup.py`, `openprogram/config_schema.py` |
| `default_provider` | Default LLM provider (written by the setup wizard) | `openprogram/setup.py` |
| `default_model` | Default model (written by the setup wizard) | `openprogram/setup.py` |
| `default_workdir` | Default working directory for agents | `openprogram/paths.py` |
| `providers` | Per-provider settings subtree (enabled models, custom models, etc.), managed by the Web UI model listing | `openprogram/providers/_config_read.py`, `openprogram/webui/_model_listing/storage.py` |
| `api_keys` | Environment variable name → API key mapping, written by the setup wizard and exported into the environment at worker startup | `openprogram/_setup_sections/sections.py`, `openprogram/webui/server.py` |
| `spec_migration_version` | One-time marker for the model-spec migration; see the code for its meaning | `openprogram/webui/_model_listing/storage.py` |

## Environment variables

Set these in the shell that launches `openprogram` (or the worker). Every one has been verified against the code; each row names where it is defined.

### Paths and instances

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_PROFILE` | State-directory profile, equivalent to `--profile`, reroutes to `~/.openprogram-<name>/` | `openprogram/paths.py` |
| `OPENPROGRAM_STATE_DIR` | Directly overrides the state directory path | `openprogram/paths.py` (referenced by memory and the rescue hints) |
| `OPENPROGRAM_HOME` | Alternative base directory for auth profiles | `openprogram/auth/profiles.py` |
| `OPENPROGRAM_WORKDIR` | Default agent working directory (takes precedence over the config's `default_workdir`) | `openprogram/paths.py` |

### Ports and web

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_BACKEND_PORT` | backend port (default 18109); below explicit flags, above the persisted preference | `openprogram/worker/lifecycle.py`, `openprogram/_cli_cmds/web.py` |
| `OPENPROGRAM_WEB_PORT` | frontend port (default 18100) | `openprogram/_cli_cmds/web.py` |
| `OPENPROGRAM_BACKEND_URL` | URL the frontend uses to reach the backend (read by Next.js rewrites); normally set automatically | `openprogram/worker/web.py` |
| `OPENPROGRAM_NO_WEB` | `1` = the worker does not start the web frontend | `openprogram/worker/web.py` |
| `OPENPROGRAM_WEB_NO_FRONTEND` | `1` = `openprogram web` skips the frontend and starts only the backend | `openprogram/_cli_cmds/web.py` |
| `OPENPROGRAM_DOCS_BASE` | Mount path of the docs site (default `/docs/`; must start and end with `/`) | `tools/docs_site/build.py` |

### Behavior switches

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_NO_AUTO_WORKER` | `1` = the TUI does not auto-launch a worker; connects only to an existing one | `openprogram/cli_ink.py` |
| `OPENPROGRAM_NO_AUTO_UPDATE` | `1` = disable auto-update | `openprogram/updater/runner.py` |
| `OPENPROGRAM_NO_SLEEP` | `1` = disable the memory sleep-consolidation scheduler | `openprogram/memory/scheduler.py` |
| `OPENPROGRAM_NO_PROGRAMS_WATCH` | `1` = disable the file watcher on the programs directory | `openprogram/functions/watcher.py` |
| `OPENPROGRAM_PROJECT_AUTOCOMMIT` | `0` = turn off project auto-commit | `openprogram/store/project/project_commit.py` |
| `OPENPROGRAM_WEBSEARCH_DISABLE` | Disable a web search provider by name (e.g. `ollama`) | `openprogram/functions/tools/web_search/providers/ollama.py` |

### LLM calls

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_MAX_RETRIES` | Runtime retry count for transient API failures (default 6) | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_EXEC_TIMEOUT_S` | Time budget in seconds for a single `runtime.exec` | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_FALLBACK_MODELS` | Comma-separated `provider/model` list; switched to in order when the main model fails | `openprogram/providers/utils/failover.py` |
| `OPENPROGRAM_PROVIDER_STREAM_RETRIES` | Maximum retries for streaming requests | `openprogram/providers/utils/stream_retry.py` |
| `OPENPROGRAM_STRICT_TOOLS` | `0` = turn off strict tool schemas (on by default) | `openprogram/providers/_schema/__init__.py` |
| `OPENPROGRAM_FORCE_IPV4` | `1` = force an IPv4 source address (for broken IPv6 networks) | `openprogram/providers/utils/http_client.py` |

### Debugging

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_DEBUG_RUNTIME` | `1` = mirror runtime logs to stderr | `openprogram/webui/server.py` |
| `OPENPROGRAM_DEBUG_REGISTRY` | `1` = show function-registry import failures | `openprogram/functions/_registry.py` |
| `OPENPROGRAM_DEBUG_DISPATCHER` | `1` = dispatcher debug logs | `openprogram/agent/dispatcher/runtime_attach.py` |
| `OPENPROGRAM_DEBUG_PROVIDER` | `1` = provider-layer debug logs | `openprogram/providers/openai_codex/openai_codex.py` |
| `OPENPROGRAM_EVENT_LOG` | `1` or a file path = append every typed event as a JSON line | `openprogram/agent/event_bus.py` |

### Others

The code holds a further batch of more internal variables (HTTP/SSE timeout tuning `OPENPROGRAM_HTTPX_*` / `OPENPROGRAM_SSE_*`, TCP keepalive `OPENPROGRAM_TCP_*`, per-provider retry counts `OPENPROGRAM_<PROVIDER>_MAX_RETRIES`, `OPENPROGRAM_TASK_WORKERS`, `OPENPROGRAM_IMAGE_DIR`, `OPENPROGRAM_BROWSER_CDP_URL`, etc.). `grep -rn "OPENPROGRAM_" openprogram/` lists the full set; every variable is commented where it is defined.
