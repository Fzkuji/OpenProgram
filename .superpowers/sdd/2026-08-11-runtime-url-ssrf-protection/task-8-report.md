# Task 8 Report: Runtime HTTP Compatibility and Consumer Acceptance

## Scope

- Added `tests/component/security/test_runtime_http_compatibility.py`.
  Its 41 hand-written fixtures cover exactly every `CONSUMER_REGISTRY` key and
  assert trust class, accepted method, scheme, port, accepted fixed/configured/
  callback origin through the real policy boundary, redirect policy, redirect
  cap, decoded-body cap, MIME prefixes, credential-origin behavior,
  owner-exception scope, and SDK disposition.
- Added `tests/component/security/test_runtime_http_acceptance.py` with real managed
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

- RED: `uv run pytest -q tests/component/security/test_runtime_http_compatibility.py tests/component/security/test_runtime_http_acceptance.py`
  returned `1 failed, 1 passed`. The incomplete fixture table contained only
  `tool.web_fetch`; the completeness assertion named the other registry keys
  as missing.
- GREEN after completing the literal table and scenarios: the same command
  returned `53 passed in 6.66s`.
- `uv run pytest -q tests/security`: exit 0, `631 passed in 84.33s`.
- `uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/programs/test_mcp_client.py`:
  exit 0, `2932 passed, 12 skipped, 1 xfailed, 4 warnings in 164.32s`.
  The four warnings are existing websockets/uvicorn deprecations.
- `uv run python scripts/check_runtime_http.py`: exit 0,
  `unregistered=0 active_unmanaged=0 registry_without_consumer=0 stale_exclusions=0`.
- `uv run ruff check tests/component/security/test_runtime_http_compatibility.py tests/component/security/test_runtime_http_acceptance.py`:
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

## Spec-review fix round 1

### F1: literal matrix now tests enforcement

- Added a complete independent literal origin set for every fixed consumer
  (including all 54 additional origins that the previous selected-origin rows
  did not represent), exact-set equality, acceptance of every listed origin,
  and rejection of a non-member origin.
- Each literal consumer row now drives `SafeClient` through rejected method,
  scheme, and origin/port paths. The matrix also has real local-socket tests
  for public credential stripping, configured MIME rejection, decoded-body
  cap enforcement, configured cross-origin redirect rejection, and owner
  exception scope. SDK dispositions are exercised through their fail-closed
  runtime guard and the executable inventory checker.
- F1 fixture RED:
  `uv run pytest -q tests/component/security/test_runtime_http_compatibility.py -k fixed_origin_fixtures`
  returned `1 failed, 42 deselected`; the exact missing key was `updater.pip`.
  After restoring its literal row, the full compatibility file was GREEN:
  `109 passed in 4.83s`.
- F1 behavior-mutation RED: disabling `_sanitize_credentials`,
  `_validate_response_headers`, and `_LimitedResponse._check` before test
  collection produced `3 failed, 65 passed`: the real local server observed a
  leaked Authorization header, and the MIME/cap tests did not raise.

### F2: proxy outage now has a direct-target sentinel

- The proxy and target are distinct local TCP servers. A decision backend maps
  only the declared-public target decision to the sentinel while preserving the
  real proxy socket. The assertion requires `httpx.RemoteProtocolError`, one
  proxy connection, and zero target connections.
- F2 RED: the first precise-error expectation used `httpx.ProxyError` and
  failed because a reset proxy connection surfaced `httpx.ReadError`. After
  making the outage handler consume the request before closing, the stable
  expected error is `httpx.RemoteProtocolError`; GREEN:
  `1 passed, 10 deselected in 1.02s`.
- F2 fallback-mutation RED: an injected direct local socket after the proxy
  error caused `target.connections == 1`, failing the zero-connection
  assertion. No external target is dialed by the test or this mutation.

### Round-1 focused verification

- `uv run pytest -q tests/component/security/test_runtime_http_compatibility.py tests/component/security/test_runtime_http_acceptance.py`:
  `120 passed in 11.57s`.
- `uv run pytest -q tests/security`: exit 0, `698 passed in 88.11s`.
- `uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/programs/test_mcp_client.py`:
  exit 0, `2932 passed, 12 skipped, 1 xfailed, 4 warnings in 192.44s`.
- Runtime inventory checker: exit 0 with all four categories zero.
- Changed-file Ruff, Ruff format check, `uv lock --check`, and `git diff --check`:
  exit 0.

## Spec-review fix round 2

### F1: every literal row drives local-socket enforcement

- Added a 41-row `SafeClient`/local HTTP-server contract.  Each row executes
  every literal allowed method and, through the managed transport, verifies its
  redirect disposition, accepted MIME, rejected MIME, streamed decoded-body
  cap, credential forwarding or stripping, and (where declared) exact
  owner-exception allow and deny behavior.  The per-row test changes only the
  test-local origin, port, and socket destination needed to keep all traffic
  local; public rows retain the managed transport's public-peer validation.
- The fixed-origin literal table remains the independent full set: every
  declared member is accepted by the policy boundary and every fixed consumer
  rejects a non-member.  Existing literal policy tests continue to cover the
  declared allowed scheme and port sets plus their rejected counterparts.
- RED, with only the named consumer implementation checks disabled:
  `channel.telegram.attachment` credential stripping,
  `tts.fixed_api` MIME validation, and
  `channel.attachment.download` decoded-body checking returned `3 failed`.
  The local server respectively observed the leaked Authorization header, did
  not produce `MIME_TYPE_FORBIDDEN`, and did not produce
  `BODY_TOO_LARGE`.  No external address is a possible socket target.
- GREEN:
  `uv run pytest -q tests/component/security/test_runtime_http_compatibility.py tests/component/security/test_runtime_http_acceptance.py`
  returned `161 passed in 32.97s`.
- SDK disposition remains bound to the executable runtime-inventory checker;
  disabled SDK consumers are also fail-closed by
  `require_active_sdk_transport`.  Existing SDK transport tests exercise the
  injected provider/MCP adapters and disabled gateway imports.

### Round-2 verification

- `uv run pytest -q tests/security`: exit 0, `739 passed in 110.73s`.
- `uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/programs/test_mcp_client.py`:
  exit 0, `2932 passed, 12 skipped, 1 xfailed, 4 warnings in 176.80s`.
  The four warnings are existing websockets/uvicorn deprecations.
- No production file changed and F2's proxy-outage sentinel was not modified.

## Quality-review fix round 3

- Added a 41-row, literal-fixture-driven `SafeClient` policy-boundary test for
  every allowed scheme and every reachable allowed port.  Configured and
  callback origins are reconstructed per allowed value; fixed consumers use
  every independent literal fixed origin and only its reachable port form.
  The test calls the real managed transport policy boundary and verifies each
  accepted value reaches resolution.  Rejected `ftp` schemes and, where the
  policy declares a finite port set, rejected port `65535` are rejected before
  resolver invocation, so the test cannot connect externally.
- RED: wrapping `_ManagedTransportBase._evaluate` so only
  `provider.configured_api` rejects `https://` produced
  `1 failed, 40 passed, 150 deselected, 1 warning` from
  `uv run pytest -q tests/component/security/test_runtime_http_compatibility.py -k allowed_scheme`.
  The failing parametrized case named `provider.configured_api` and its
  declared `https://configured.example.test:17654` URL.  The warning is
  pytest assertion-rewrite state from the in-process mutation runner.
- GREEN: the same focused new test returned `41 passed, 150 deselected in
  1.82s`; the complete Task 8 focused pair returned `202 passed in 34.93s`.
- `uv run pytest -q tests/security`: exit 0, `780 passed in 112.33s`.
- `uv run pytest -q tests/meta_functions tests/providers tests/unit tests/webui tests/integration/programs/test_mcp_client.py`:
  exit 0, `2932 passed, 12 skipped, 1 xfailed, 4 warnings in 163.91s`.
  The warnings are existing websockets/uvicorn deprecations.
- No production or documentation file changed; the existing socket contract
  remains responsible for transport-level redirect, MIME, cap, credential,
  and owner-exception behavior.
