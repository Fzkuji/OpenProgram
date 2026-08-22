# Configuration

The keys in `~/.openprogram/config.json`, what `openprogram config` can read and write, and the environment variable roundup. For the everyday entry point to changing settings, see [Configuration and data directory](../server/configuration.md).

## What openprogram config can read and write

```bash
openprogram config list              # every setting: value, group, apply mode
openprogram config get ui.web_port
openprogram config set ui.web_port 8101
```

The settings registry is defined in `openprogram/config_schema.py` (the single source of truth; the setup wizard, the TUI settings page, and the Web settings page all render from it). Every setting is labeled with an apply mode: `live` takes effect immediately, `next_start` takes effect the next time the worker starts.

| key | Group | Meaning | Default | Applies |
|-----|------|------|------|------|
| `ui.web_port` | Ports | legacy alias for the single port (kept for the deprecation window) | 18100 | next start |
| `ui.open_browser` | Ports | whether `openprogram web` opens the browser automatically | true | next start |
| `search.default_provider` | Search | default web search provider; `auto` picks the highest-priority configured one | auto | live |
| `memory.backend` | Memory | `local` (on-disk memory) or `none` (no prompt memory, recall, automatic writes, organizer, or memory threads) | local | next start |
| `memory.writer.model` | Memory | optional `provider/model` override for background writing; empty uses the default chat agent's provider, model, and credentials | empty | live |
| `sandbox.mode` | Sandbox | `danger-full-access`, or `workspace-write` to apply the host-native sandbox to local model-driven commands: writes are limited to the working directory/configured roots, deny-read paths are blocked, and network is disabled | workspace-write | live |
| `sandbox.writable_roots` | Sandbox | extra directories a sandboxed command may write, as a JSON list | [] | live |
| `sandbox.deny_read` | Sandbox | globs a sandboxed command cannot read; defaults include credential paths. Linux cannot enforce middle-wildcard patterns such as `**/.env`: use an exact path or a concrete directory deny such as `/absolute/path/to/secrets/**` for sensitive content | see `openprogram config get sandbox.deny_read` | live |
| `sandbox.deny_write` | Sandbox | globs a sandboxed command cannot write, on top of the always-blocked function-watcher directory | [] | live |
| `sandbox.network` | Sandbox | whether a sandboxed command has network access | false | live |
| `sandbox.pass_env` | Sandbox | environment variable names to pass through besides the built-in allowlist | [] | live |
| `sandbox.apply_in_bypass` | Sandbox | off (default): bypass permissions means full access — the configurable sandbox limits do not apply, matching Claude Code's `--dangerously-skip-permissions`; on: those limits stay enforced even in bypass mode | false | live |
| `sandbox.unavailable_policy` | Sandbox | `refuse` fails the command when the platform backend is missing or cannot create its required isolation; `warn` runs it unsandboxed | refuse | live |
| `tools.disabled.<name>` | Tools | per-tool switch; written as members of the `tools.disabled` list | all enabled | live |
| `agent.output_style` | Agent | how replies are written; appends a block to the system prompt. See [Output styles](output-styles.md) | default | live |
| `providers.<name>` | Providers | read-only status row (configured or not); configure with `openprogram providers login` or the Web UI | — | — |

The local sandbox is host-native: Seatbelt on macOS and bubblewrap on Linux. Windows and unsupported platforms refuse commands while the sandbox is enabled unless the owner explicitly selects the unsafe `sandbox.unavailable_policy=warn` or sets `sandbox.mode=danger-full-access`. Docker is not an automatic fallback.

## Top-level keys in config.json

The top-level keys actually written to `~/.openprogram/config.json` (do not edit by hand — go through `openprogram config set`, the setup wizard, or the Web UI):

| Key | Meaning | Code |
|----|------|------|
| `ui` | `{port, web_port, open_browser}`, see the table above | `openprogram/config_schema.py` |
| `search` | `{default_provider}` | `openprogram/setup.py` |
| `memory` | `{backend, writer: {model}}`, see the table above | `openprogram/config_schema.py`, `openprogram/memory/` |
| `tools` | `{disabled: [tool name, ...]}` | `openprogram/setup.py`, `openprogram/config_schema.py` |
| `sandbox` | `{mode, writable_roots, deny_read, deny_write, network, pass_env, apply_in_bypass, unavailable_policy}`, see the table above | `openprogram/sandbox/__init__.py`, `openprogram/config_schema.py` |
| `default_provider` | Default LLM provider (written by the setup wizard) | `openprogram/setup.py` |
| `default_model` | Default model (written by the setup wizard) | `openprogram/setup.py` |
| `default_workdir` | Default working directory for agents | `openprogram/paths.py` |
| `providers` | Per-provider settings subtree (enabled models, custom models, etc.), managed by the Web UI model listing | `openprogram/providers/_config_read.py`, `openprogram/providers/storage.py` |
| `api_keys` | Environment variable name → API key mapping, written by the setup wizard and exported into the environment at worker startup. Used for web-search / TTS keys; LLM provider keys live in the credential store (`openprogram providers login`), not here | `apps/cli/python/openprogram_cli/_impl/setup_sections/sections.py`, `apps/server/openprogram_server/server.py` |
| `spec_migration_version` | One-time marker for the model-spec migration; see the code for its meaning | `openprogram/providers/storage.py` |

## Environment variables

Set these in the shell that launches `openprogram` (or the worker). Every one has been verified against the code; each row names where it is defined.

### Paths and instances

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_PROFILE` | State-directory profile, equivalent to `--profile`, reroutes to `~/.openprogram-<name>/` | `openprogram/paths.py` |
| `OPENPROGRAM_HOME` | Alternative base directory for auth accounts | `openprogram/auth/accounts.py` |
| `OPENPROGRAM_WORKDIR` | Default agent working directory (takes precedence over the config's `default_workdir`) | `openprogram/paths.py` |

### Ports and web

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_WEB_PORT` | the single worker port (default 18100); below explicit flags, above the persisted preference | `openprogram/worker/lifecycle.py`, `apps/cli/python/openprogram_cli/_impl/commands/web.py` |
| `OPENPROGRAM_NO_WEB` | `1` = the worker skips the frontend build gate and does not serve the web UI | `openprogram/worker/runner.py` |
| `OPENPROGRAM_WEB_NO_FRONTEND` | `1` = `openprogram web` skips the frontend and starts only the backend | `apps/cli/python/openprogram_cli/_impl/commands/web.py` |
| `OPENPROGRAM_DOCS_BASE` | Mount path of the docs site (default `/docs/`; must start and end with `/`) | `scripts/docs_site/build.py` |

### Behavior switches

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_NO_AUTO_WORKER` | `1` = the TUI does not auto-launch a worker; connects only to an existing one | `apps/cli/python/openprogram_cli/_impl/ink.py` |
| `OPENPROGRAM_NO_SLEEP` | `1` = disable the memory sleep-consolidation scheduler | `openprogram/memory/scheduler.py` |
| `OPENPROGRAM_NO_PROGRAMS_WATCH` | `1` = disable the file watcher on the programs directory | `openprogram/programs/watcher.py` |
| `OPENPROGRAM_PROJECT_AUTOCOMMIT` | `0` = turn off project auto-commit | `openprogram/store/project/project_commit.py` |
| `OPENPROGRAM_WEBSEARCH_DISABLE` | Disable a web search provider by name (e.g. `ollama`) | `openprogram/programs/tools/web/web_search/providers/ollama.py` |

### LLM calls

| Variable | Purpose | Code |
|------|------|------|
| `AGENTIC_PROVIDER` / `AGENTIC_MODEL` | Provider / model that `detect_provider()` (and thus `create_runtime()`) picks first, before config-file and CLI detection | `openprogram/providers/registry.py` |
| `OPENPROGRAM_MAX_RETRIES` | Runtime retry count for transient API failures (default 6) | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_RETRY_BACKOFF_BASE` | Base seconds for the exponential retry backoff (default 1.5) | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_EXEC_TIMEOUT_S` | Default wall-clock budget in seconds for every `runtime.exec` when the caller passes no `timeout_s` (unset or `0` = unbounded) | `openprogram/agentic_programming/runtime.py` |
| `OPENPROGRAM_FALLBACK_MODELS` | Failover chain used when the main model fails before any output. Unset = the other enabled models of the same provider (max 2); a comma-separated `provider/model` list overrides it and may cross providers; `off` disables failover | `openprogram/providers/utils/failover.py` |
| `OPENPROGRAM_PROVIDER_STREAM_RETRIES` | Maximum retries for streaming requests | `openprogram/providers/utils/stream_retry.py` |
| `OPENPROGRAM_STRICT_TOOLS` | `0` = turn off strict tool schemas (on by default) | `openprogram/providers/_schema/__init__.py` |
| `OPENPROGRAM_FORCE_IPV4` | `1` = force an IPv4 source address (for broken IPv6 networks) | `openprogram/providers/utils/http_client.py` |

### Debugging

| Variable | Purpose | Code |
|------|------|------|
| `OPENPROGRAM_DEBUG_RUNTIME` | `1` = mirror runtime logs to stderr | `openprogram/webui/server.py` |
| `OPENPROGRAM_DEBUG_REGISTRY` | `1` = show function-registry import failures | `openprogram/programs/_registry.py` |
| `OPENPROGRAM_DEBUG_DISPATCHER` | `1` = dispatcher debug logs | `openprogram/agent/dispatcher/runtime_attach.py` |
| `OPENPROGRAM_DEBUG_PROVIDER` | `1` = provider-layer debug logs | `openprogram/providers/openai_codex/openai_codex.py` |

### Others

The code holds a further batch of more internal variables (HTTP/SSE timeout tuning `OPENPROGRAM_HTTPX_*` / `OPENPROGRAM_SSE_*`, TCP keepalive `OPENPROGRAM_TCP_*`, per-provider retry counts `OPENPROGRAM_<PROVIDER>_MAX_RETRIES`, `OPENPROGRAM_JOB_WORKERS`, `OPENPROGRAM_IMAGE_DIR`, `OPENPROGRAM_BROWSER_CDP_URL`, etc.). `grep -rn "OPENPROGRAM_" openprogram/` lists the full set; every variable is commented where it is defined.
