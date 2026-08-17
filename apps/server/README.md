# OpenProgram Server

FastAPI application assembly for OpenProgram's HTTP, WebSocket, and static Web
surfaces. It imports the reusable Agent Core from `openprogram/`; the core does
not import this application during ordinary SDK use.

The canonical application package is `openprogram_server`. Server transport
source lives under `openprogram_server/_webui/` and is loaded through the
established `openprogram.webui.*` names so compatibility imports and shared
mutable state keep one module identity. The root compatibility package contains
no duplicate route or WebSocket implementation.

## Verify

```bash
uv run --locked pytest -q tests/contracts/repository/test_apps_layout.py \
  tests/component/webui/test_healthz.py
```
