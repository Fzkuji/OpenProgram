# Terminal TUI

See [tool permission modes and live changes](../capabilities/permissions.md) for approval behavior and changes during a task.

The full chat interface for using OpenProgram without leaving the terminal. This page covers entering and exiting, keyboard shortcuts, and slash commands.

![Terminal TUI](../images/tui_hero.png)

## Entering and exiting

```bash
openprogram tui      # straight into the terminal chat (alias: openprogram chat)
openprogram          # bare invocation first asks: terminal UI or web UI
```

The release CLI always has the Python terminal interface. Source-development installations can additionally build the Node.js Ink interface on macOS and Linux. Both connect to the same local worker over WebSocket and share sessions with the Web UI. Windows release installation is not supported.

Exit: `/quit`, or press `Ctrl-C` twice quickly while idle.

To resume a past session: use `/resume` inside the TUI to pick one; session ids are listed by `openprogram sessions list`. (The `openprogram --resume <id>` flag currently takes effect only for `--print` one-shots, not when launching the interactive TUI.)

## Keys

| Key | Action |
|---|---|
| `Enter` | Send |
| `Alt+Enter` | Newline |
| `Esc` | Clear the input line; abort the current turn while generating |
| `Ctrl-C` (while generating) | Three-stage stop: first press warns, second stops gracefully, third forces |
| `Ctrl-C` double press (idle) | Exit |
| `↑` / `↓` | Input history; navigate up/down when the completion menu is open |
| `Tab` | Accept file / slash-command completion |
| `→` (at end of line) or `Ctrl+E` | Accept the autocomplete suggestion |
| `Ctrl+R` | Search saved contexts |
| `Shift+Tab` | Cycle permission profiles (ask → acceptEdits → plan → auto) |
| `Ctrl+K` | Command palette (covers all slash commands) |
| `PageUp` / `PageDown`, `Ctrl+U` / `Ctrl+D` | Scroll back by page / half page |
| `Home` / `End` | Jump to top / bottom |

## Slash commands

Type `/` to trigger completion. Common ones:

| Command | Action |
|---|---|
| `/help` | Command list |
| `/model`, `/fetch-models` | Switch model, re-fetch the model list |
| `/effort` | Adjust thinking effort (levels in [thinking effort](../models/thinking-effort.md)) |
| `/new`, `/resume`, `/sessions`, `/session` | New session, resume, session list, current session info |
| `/rewind` | Roll the session back to a message |
| `/compact`, `/context`, `/clear` | Compact context, view context, clear screen |
| `/permissions`, `/sandbox` | Permission profiles and sandbox |
| `/login <provider>`, `/logout` | Provider login / logout (see [auth and credentials](../models/auth.md)) |
| `/agents`, `/agent` | Manage / switch agents |
| `/mcp`, `/tools`, `/memory` | Same data as the corresponding Web UI pages |
| `/cost` | Token usage for this session |
| `/export`, `/copy` | Export the session, copy a reply |
| `/config`, `/theme`, `/bell` | Settings, theme, notification sound |
| `/doctor` | Health check |
| `/channel`, `/attach`, `/detach`, `/connections` | Chat-channel hookup and session routing |
| `/quit` | Exit |

Also available: `/search`, `/review`, `/diff`, `/init`, `/browser`, `/welcome`. The `/help` output is the authoritative full list.

Beyond these built-ins, the completion menu also lists every command from the unified command registry — skills, MCP prompts, plugin commands, and your own command files under `~/.openprogram/commands/` or `<project>/.openprogram/commands/` (markdown with optional YAML frontmatter). Running one expands its body and sends it as the message, exactly like the Web composer: the TUI and the Web UI read the same registry, so a command defined once appears in both.
