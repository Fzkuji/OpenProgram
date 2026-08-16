# Interfaces

OpenProgram has four user interfaces: the macOS Desktop App, the Web UI in an external browser, the terminal TUI, and one-shot command-line invocations. This page explains how they relate and helps you pick an entry point.

## Four clients, one service

All four interfaces share the same local background service (called the worker in the code): a resident process hosting the FastAPI + WebSocket backend and the web UI itself, all on a single port (18100 by default), plus optional chat-channel adapters. Desktop embeds the same Web UI and adds native panes for the built-in Browser and Terminal. The Web UI and terminal TUI connect to the worker directly; if no worker is running, Desktop or the TUI starts one automatically.

Sessions all live in `~/.openprogram/sessions/` (each session is a git repository), and all four interfaces read and write the same store. As a result:

- A chat started in the terminal shows up in the Web UI sidebar; click it to continue.
- A Web session can be resumed in the terminal via `/resume` inside the TUI, or continued non-interactively with `openprogram --resume <session-id> --print "..."`. (The `--resume` flag does not yet select the session when launching the interactive TUI — use `/resume` there.)
- Conversations from `openprogram --print "..."` one-shots are also written to the session store and can be revisited later in any interface.

Worker management commands: `openprogram status` / `stop` / `restart`; `openprogram worker install` registers it as a login-launch service. See `openprogram -h` for details.

## The four interfaces

| Interface | How to enter | Best for |
|---|---|---|
| [macOS Desktop](desktop.md) | Open `/Applications/OpenProgram.app` | Multi-pane chat, Files, built-in Browser, Terminal, and Agent control of visible internal webpages |
| [Web UI](web.md) | `openprogram web`, open `http://localhost:18100` in a browser | Daily main interface: chat, DAG branch view, function / skill / MCP / memory management, settings |
| [Terminal TUI](tui.md) | `openprogram tui` (bare `openprogram` first asks terminal vs web) | Full chat without leaving the terminal: slash commands, permission-profile switching, scrollable history |
| [CLI one-shot](cli.md) | `openprogram --print "..."` | Scripting, being called from other programs, quick one-off questions |

## Isolated workspaces

`--profile <name>` (or the `OPENPROGRAM_PROFILE` environment variable) switches the entire state directory from `~/.openprogram/` to `~/.openprogram-<name>/` — config, sessions, logs, and credentials are all isolated, and each profile has its own worker. Use it to run multiple independent environments in parallel.
