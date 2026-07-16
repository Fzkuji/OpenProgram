# Configuration

All of OpenProgram's state lives in a single directory, `~/.openprogram/`. This page covers what is in it, how `openprogram config` reads and writes settings, and how to isolate multiple sets of state with profiles.

## What lives in ~/.openprogram/

The main files and subdirectories, grouped by purpose:

| Path | Contents |
|------|------|
| `config.json` | User settings: ports, default model, provider configuration, disabled tools, etc. — see the [configuration reference](../reference/config.md) |
| `sessions/`, `sessions-git/` | Chat session data and its git archive |
| `agents/`, `agents.json` | Agent definitions (persona, model, skills) |
| `auth/` | Provider credential store |
| `skills/` | Installed skills (SKILL.md directories) |
| `plugins/` | Installed plugins |
| `mcp_servers.json` | MCP server configuration |
| `memory/` | Persistent memory (wiki + journal) |
| `channels/` | Chat channel bot state (Telegram, Discord, WeChat, etc.) |
| `browser-states/`, `chrome-profile/` | Browser tool login state and the sidecar Chrome profile |
| `projects/`, `worktrees/`, `shadow-git/` | Project workspaces and git worktree state |
| `logs/`, `worker.log` | Logs; also worker runtime files such as `worker.pid` / `worker.port` / `worker.lock` |
| `models/`, `cache/`, `tool_results/`, `usage.db` | Model catalog cache, general cache, tool results, usage database |

## openprogram config

```bash
openprogram config list              # list every setting: value, group, when it applies
openprogram config get <key>         # read one setting, e.g. ui.port
openprogram config set <key> <value> # change one setting
```

Every setting has an apply mode: `live` (takes effect immediately) or `next start` (takes effect the next time the worker starts; `config list` labels each one). Core keys:

| key | Meaning | Default | Applies |
|-----|------|------|------|
| `ui.port` | backend (FastAPI) port | 18109 | next start |
| `ui.web_port` | frontend (Web UI) port | 18100 | next start |
| `ui.open_browser` | whether `openprogram web` opens the browser automatically | true | next start |
| `search.default_provider` | default web search provider (`auto` picks the highest-priority configured one) | auto | live |
| `memory.backend` | memory backend: `local` (on disk) or `none` (disabled) | local | next start |
| `tools.disabled.<name>` | per-tool switch (written into the `tools.disabled` list) | all enabled | live |

`config list` also shows read-only `providers.<name>` status rows — they cannot be changed with `config set`; configure them with `openprogram providers login` or the Providers page in the Web UI.

## Port shortcut

`openprogram ports` is the dedicated writer for `ui.port` / `ui.web_port`:

```bash
openprogram ports                        # view
openprogram ports --backend 8102 --frontend 8101   # persist a change
```

## Network proxy

All LLM provider traffic resolves its proxy the same way, in this order:

1. **`OPENPROGRAM_PROXY_URL`** — explicit override. When set, every provider
   request goes through it. Accepts `http://`, `https://`, or `socks5://`
   URLs. `NO_PROXY` bypasses still apply.
2. **Standard environment variables** — `http_proxy` / `HTTP_PROXY`,
   `https_proxy` / `HTTPS_PROXY`, `all_proxy` / `ALL_PROXY`, with
   `no_proxy` / `NO_PROXY` as the bypass list (hostnames, domain suffixes,
   or `*`). On macOS and Windows, the operating system's proxy settings are
   used when none of these variables are set — the same fallback Python's
   standard library applies.

SOCKS proxies are supported out of the box (`httpx[socks]` is a hard
dependency). CLI-backed providers (Claude Code, Codex CLI, Gemini CLI) run
as subprocesses that inherit your shell environment, so the external CLI
applies its own proxy handling.

`openprogram rescue` reports the resolved proxy configuration and flags a
SOCKS proxy whose support package is missing.

## Multiple instances: --profile

`--profile <name>` (or the environment variable `OPENPROGRAM_PROFILE`) reroutes config, sessions, and logs to `~/.openprogram-<name>/`, so parallel workspaces share no state:

```bash
openprogram --profile dev            # run an independent instance on ~/.openprogram-dev/
OPENPROGRAM_PROFILE=dev openprogram status
```

Combined with different `OPENPROGRAM_BACKEND_PORT` / `OPENPROGRAM_WEB_PORT` values, several services can run at once. For installation, see [Profiles](../install/profiles.md).
