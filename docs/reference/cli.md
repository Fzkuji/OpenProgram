# CLI reference

A quick reference for every `openprogram` subcommand. Each command has its own help via `openprogram <command> -h`; subcommand verbs nest one level deeper, e.g. `openprogram logs tail -h`.

## Global usage

```bash
openprogram                      # open the terminal chat UI (TUI)
openprogram --print "..."        # one-shot prompt: send, print the reply, exit
openprogram --resume <id>        # resume a previous CLI chat session
openprogram --profile <name>     # state-directory profile, reroutes to ~/.openprogram-<name>/
```

| Flag | What it does |
|------|------|
| `--print PROMPT` | One-shot prompt; prints the reply and exits |
| `--profile PROFILE` | State-directory profile, equivalent to the `OPENPROGRAM_PROFILE` environment variable |
| `--resume SESSION_ID` | Resume a session; find ids with `openprogram sessions list` or the Web UI sidebar |

## Chat and running

| Command | What it does | Key flags |
|------|------|----------|
| `openprogram` | Open the terminal chat UI; auto-launches a worker if none is running | — |
| `openprogram web` | Start the service and open the browser UI (`http://localhost:18100`) | `--port` (backend, default 18109), `--web-port` (frontend, default 18100), `--no-browser` |

## Background service

| Command | What it does |
|------|------|
| `status` | Is the background service running (PID, port, uptime) |
| `stop` | Stop the background service |
| `restart` | Restart (after code / config changes) |

The `worker` subcommands offer finer control:

| Command | What it does |
|------|------|
| `worker run` | Run the worker in the foreground (blocking), for debugging; Ctrl-C to stop |
| `worker start` | Start a worker in the background and return |
| `worker stop` | Stop (SIGTERM, escalating to SIGKILL if needed) |
| `worker restart` | Stop and start a fresh one |
| `worker status` | Running or not, PID, port, uptime |
| `worker install` | Install as a system service (macOS launchd / Linux systemd --user); starts at login, restarts after a crash |
| `worker uninstall` | Remove the system service |

## Setup and configuration

| Command | What it does | Key flags / verbs |
|------|------|----------|
| `setup` | First-run setup wizard | `menu` opens the interactive picker; give a section name to jump straight there (model / tools / agent / skills / ui / memory / profile / search / tts / channels / backend) |
| `config` | View / change settings | `list` (every setting: value, group, apply mode), `get <key>`, `set <key> <value>` |
| `ports` | View / persist the Web UI ports | `--backend PORT` (default 18109), `--frontend PORT` (default 18100) |
| `completion` | Print a shell completion script | `bash` / `zsh` / `powershell` / `pwsh` |

### providers — LLM providers and credentials

| Verb | What it does |
|------|------|
| `login <provider>` | Log in to a provider; `--api-key` / `--api-key-stdin` supply the key non-interactively, `--profile` selects the credential profile |
| `logout` | Remove a provider's credentials |
| `list` | List credential pools by profile |
| `available` (alias `search`) | List every configurable provider, optionally filtered with QUERY |
| `status` | Check a provider's current credentials |
| `use` | Set which account (profile) a provider uses |
| `discover` / `adopt` | Scan external credential sources / import them into the credential store |
| `doctor` | Diagnose credentials (expiry, refresh, cooldown, conflicts) |
| `setup` | Interactive first-time setup |
| `aliases` | List provider short-name aliases |
| `profiles` | Credential profile management |
| `migrate` | Migrate stored credentials to the current format |

`openprogram providers` with no verb prints the status table for all current credentials.

### mcp — MCP servers

| Verb | What it does |
|------|------|
| `list` | List every configured MCP server and its status |
| `show` | Show a server's tools and full schemas |
| `add` | Add a stdio command server; writes `mcp_servers.json` and starts it immediately |
| `rm` | Remove (stop + delete config) |
| `restart` / `enable` / `disable` | Restart / enable and start / stop and mark disabled (config kept) |
| `edit` | Edit `mcp_servers.json` directly with `$EDITOR` |
| `test` | Start a config temporarily to verify it comes up and returns its tool list, without persisting |

### browser — browser tools

| Verb | What it does |
|------|------|
| `install` | Install browser tool dependencies (Playwright + Chromium, patchright/camoufox, agent-browser); pick one target or `all` |
| `status` | Show install state, whether the sidecar Chrome is running, saved login count |
| `refresh` | Re-copy the real Chrome profile into the sidecar (after logging in to a new site in your main Chrome) |
| `reset` | Full reset: kill the sidecar, clear the profile + login state + port files |
| `list` / `rm` | List / delete saved logins under `~/.openprogram/browser-states/` |

## Content management

### agents

| Verb | What it does |
|------|------|
| `list` / `show` / `add` / `rm` | List / view / create / delete agents (deleting removes all its sessions too) |
| `set-default` | Set as the default agent |

### sessions

| Verb | What it does |
|------|------|
| `list` | List every session across all agents |
| `resume` | Answer a waiting session |
| `attach` / `detach` | Route a channel user's messages into a session / remove the alias |
| `aliases` | List all session-to-channel-user aliases |

### programs

| Verb | What it does |
|------|------|
| `run <name>` | Run a program; `--arg key=value` (repeatable), `--provider`, `--model` |
| `list` | List saved programs |
| `available` | List installable programs and installed third-party harnesses |
| `install` / `uninstall` | Install / uninstall a program (gui/research/wiki/all) or a third-party harness (git URL / owner/repo) |

### skills

| Verb | What it does |
|------|------|
| `list` | List discovered skills |
| `search` / `install` | Search / install skills from the discovery source (ClawHub by default) |
| `update` | Re-pull stale skills (compares SKILL.md hashes) |
| `remove` | Delete an installed skill |
| `doctor` | Scan the skills directory for problems |

### plugins

| Verb | What it does |
|------|------|
| `list` / `search` | List installed plugins / search the marketplace |
| `install` / `uninstall` / `update` | Install from pip / npm / git / a path, uninstall, upgrade |
| `enable` / `disable` | Enable / disable |

### channels — chat channel bots

| Verb | What it does |
|------|------|
| `list` | Enable and configuration status per platform |
| `setup` | Interactive wizard: pick a channel, log in (QR code / token), bind an agent |
| `accounts` | Manage channel bot accounts (WeChat, Telegram, etc.) |
| `bindings` | Route inbound channel messages to agents |

### memory — persistent memory

| Verb | What it does |
|------|------|
| `status` | Path, entry count, last sleep time |
| `recall` | Search the wiki + recent journal, print raw snippets |
| `show` / `edit` | Print / edit a wiki page with `$EDITOR` |
| `sleep` | Run a sleep consolidation pass now (light → deep → REM) |
| `reflections` | Print the latest entries of `wiki/reflections.md` |
| `export` | tar+gzip the whole memory directory to a given path |

## Maintenance

| Command | What it does | Key flags / verbs |
|------|------|----------|
| `doctor` | End-to-end health check | `--json` for JSON output |
| `rescue` | Diagnose problems and print the fix commands directly | — |
| `logs` | View logs | `list`; `tail [name]` (`-n` line count, `-f` follow); `path [name]`. name is worker / runtime / ink, default worker |
| `update` | Check for and apply updates | `--check` only checks; `--force` bypasses the 6-hour throttle |
