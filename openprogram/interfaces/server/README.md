# OpenProgram Server

FastAPI application assembly for OpenProgram's HTTP, WebSocket, and static Web
surfaces: the package `openprogram.interfaces.server`. It imports the reusable
Agent Core from `openprogram/`; the core does not import this application
during ordinary SDK use.

`server.py` assembles the app; routes (`routes/`), WebSocket actions
(`ws_actions/`), the Server namespace modules (`server_runtime/`), owner
authentication and static hosting live beside it in this package. Release
staging copies the built Web export into `_frontend/`, which ships as package
data.
`python -m openprogram.interfaces.server` starts a foreground server.

## Verify

```bash
uv run --locked pytest -q tests/contracts/repository/test_apps_layout.py \
  tests/component/webui/runtime/test_healthz.py
```
