# Task 8 Report: Runtime HTTP Compatibility and Consumer Acceptance

## Scope

- Added `tests/security/test_runtime_http_compatibility.py`.
  Its 41 hand-written fixtures cover exactly every `CONSUMER_REGISTRY` key and
  assert trust class, accepted method, scheme, port, accepted fixed/configured/
  callback origin through the real policy boundary, redirect policy, redirect
  cap, decoded-body cap, MIME prefixes, credential-origin behavior,
  owner-exception scope, and SDK disposition.
- Added `tests/security/test_runtime_http_acceptance.py` with real managed
  client and local socket scenarios only. No external network, credential, or
  keychain access is used. Test-only public-peer mapping preserves the real
  managed transport's peer check while routing local fixture traffic.
- No production file changed. Every declared supported behavior was already
  accepted by the registry and managed client, so `safe_http.py` needed no
  registry adjustment.

## Acceptance scenarios

- Public CDN `download()` through the managed client.
- GitHub catalog and updater same-origin redirects.
- Telegram fixed API origin and same-origin credential behavior.
- OpenAI-compatible localhost provider and localhost MCP configured origins.
- Private enterprise configured service with only its exact owner exception.
- Enforcing policy-proxy outage with no direct target fallback.
- Provider-origin failover rejection, distinct shared clients/transports, and
  no credential delivery to the second origin.
- Exact IPv4 and IPv6 loopback callback origins.

## TDD and verification

- RED: `uv run pytest -q tests/security/test_runtime_http_compatibility.py tests/security/test_runtime_http_acceptance.py`
  returned `1 failed, 1 passed`. The incomplete fixture table contained only
  `tool.web_fetch`; the completeness assertion named the other registry keys
  as missing.
- GREEN after completing the literal table and scenarios: the same command
  returned `53 passed in 6.66s`.
- `uv run pytest -q tests/security`: exit 0, `631 passed in 84.33s`.
- `uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/test_mcp_client.py`:
  exit 0, `2932 passed, 12 skipped, 1 xfailed, 4 warnings in 164.32s`.
  The four warnings are existing websockets/uvicorn deprecations.
- `uv run python scripts/check_runtime_http.py`: exit 0,
  `unregistered=0 active_unmanaged=0 registry_without_consumer=0 stale_exclusions=0`.
- `uv run ruff check tests/security/test_runtime_http_compatibility.py tests/security/test_runtime_http_acceptance.py`:
  exit 0.
- `uv lock --check` and `git diff --check`: exit 0.

## Concern

The brief's whole-repository Ruff command exits nonzero with six S110 findings:
three in `openprogram/acp/server.py`, one in `openprogram/attachments.py`, and
two in `openprogram/lsp/client.py`. The same command was reproduced in a
temporary detached clean BASE worktree at `ee6cc1bb1e09d2b2ee324b501d45a3ad0056cfa1`;
Task 8 does not modify those files.

## Commit

`test(security): verify runtime HTTP compatibility`
