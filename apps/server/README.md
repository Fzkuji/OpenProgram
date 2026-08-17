# OpenProgram Server

FastAPI application assembly for OpenProgram's HTTP, WebSocket, and static Web
surfaces. It imports the reusable Agent Core from `openprogram/`; the core does
not import this application during ordinary SDK use.

The canonical Python package is `openprogram_server`. Existing
`openprogram.webui` imports remain compatibility entry points while the route
modules move in later reviewed batches.

## Verify

```bash
uv run --locked pytest -q tests/contracts/repository/test_apps_layout.py \
  tests/component/webui/test_healthz.py
```
