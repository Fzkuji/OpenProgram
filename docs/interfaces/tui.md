# Terminal TUI

The full chat interface for using OpenProgram without leaving the terminal. This page covers entering and exiting, keyboard shortcuts, and slash commands.

![Terminal TUI](../images/tui_hero.png)

## Entering and exiting

```bash
openprogram          # no arguments enters the TUI
```

On macOS / Linux the TUI is implemented in Node.js (Ink) and connects to the local worker over WebSocket (starting one automatically if none is running); on Windows it falls back to a simpler Rich REPL. Sessions are shared with the Web UI, see the [interfaces overview](README.md).

Exit: `/quit`, or press `Ctrl-C` twice quickly while idle.

To resume a past session: use `/resume` inside the TUI to pick one; session ids are listed by `openprogram sessions list`.

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

The Windows Rich REPL supports only a smaller subset (`/help`, `/web`, `/model`, `/functions`, `/login`, `/attach`, `/detach`, `/connections`, `/agent`, `/new`, `/copy`, `/quit`); it can also exit via `Ctrl-C` or `Ctrl-D`.
