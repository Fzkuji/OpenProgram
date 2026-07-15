# Interfaces

OpenProgram can be used three ways: the Web UI in a browser, the TUI in a terminal, and one-shot invocations on the command line. This page explains how the three relate and helps you pick an entry point.

## Three clients, one service

All three interfaces share the same local background service (called the worker in the code): a resident process hosting the FastAPI + WebSocket backend (port 18109 by default) plus optional chat-channel adapters. The Web UI and the terminal TUI both connect to it over WebSocket; if no worker is running, the TUI starts one automatically.

Sessions all live in `~/.openprogram/sessions/` (each session is a git repository), and all three interfaces read and write the same store. As a result:

- A chat started in the terminal shows up in the Web UI sidebar; click it to continue.
- A Web session can be resumed in the terminal via `/resume` inside the TUI, or continued non-interactively with `openprogram --resume <session-id> --print "..."`. (The `--resume` flag does not yet select the session when launching the interactive TUI — use `/resume` there.)
- Conversations from `openprogram --print "..."` one-shots are also written to the session store and can be revisited later in any interface.

Worker management commands: `openprogram status` / `stop` / `restart`; `openprogram worker install` registers it as a login-launch service. See `openprogram -h` for details.

## The three interfaces

| Interface | How to enter | Best for |
|---|---|---|
| [Web UI](web.md) | `openprogram web`, open `http://localhost:18100` in a browser | Daily main interface: chat, DAG branch view, function / skill / MCP / memory management, settings |
| [Terminal TUI](tui.md) | `openprogram tui` (bare `openprogram` first asks terminal vs web) | Full chat without leaving the terminal: slash commands, permission-profile switching, scrollable history |
| [CLI one-shot](cli.md) | `openprogram --print "..."` | Scripting, being called from other programs, quick one-off questions |

## Isolated workspaces

`--profile <name>` (or the `OPENPROGRAM_PROFILE` environment variable) switches the entire state directory from `~/.openprogram/` to `~/.openprogram-<name>/` — config, sessions, logs, and credentials are all isolated, and each profile has its own worker. Use it to run multiple independent environments in parallel.
