# `openprogram/host/resources/`

> Shared resource contracts and provider dispatch.

## Files in this directory

- **`background_browser.py`** — The browser the runtime starts itself, with no window, when no App window can open a page
- **`desktop_link.py`** — The link between the core and the desktop windows a client keeps open
- **`native_window.py`** — The native app window an Agent operates, for the conversation's preview
- **`registry.py`** — Capability validation and dispatch; providers retain execution authority
- **`session.py`** — Session-attributed resource uses reported by integrations, not host discovery
- **`terminal.py`** — Single-use invocation tickets for the existing native Desktop terminals

## Sub-packages

- **`providers/`** — Built-in resource adapters; an application adds its own provider when it loads

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
