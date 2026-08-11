# Task 6 Report: Runtime Service and SDK HTTP Transports

## Status and commits

Complete. Baseline: `26cf395c78bd623ded1763645164e25cfc2bcd98`.

- Frozen MCP v1 lock synchronization: `12f9a1c4`
- Task 6 implementation: `ec762e22`
- Managed-transport unit-test seam compatibility: `dca469b8`
- Dev test-interpreter dependency and final report: this commit

BASE `uv lock --check` failed with `The lockfile at uv.lock needs to be
updated`. The declared dependency is `mcp>=1,<2`, while BASE locked MCP v2.
The lock-only commit is the mechanical result of synchronizing the existing
`pyproject.toml`; no dependency constraint was added or widened. A fresh
`uv sync --frozen --extra dev` installed 88 packages and reported `mcp 1.29.0`.

## Dispositions and entry audit

| Registry key | Disposition | Enforcement |
|---|---|---|
| `provider.openai.sdk` | injected transport | OpenAI public SDK receives scoped `SafeAsyncClient` |
| `provider.anthropic.sdk` | injected transport | Anthropic public SDK receives scoped shared managed client; construction errors propagate |
| `provider.google.sdk` | injected transport | Google `HttpOptions` receives managed sync and async clients |
| `mcp.configured.http` | injected transport | modern MCP v1 receives managed `http_client`; old v1 receives managed factory |
| `mcp.configured.sse` | injected transport | MCP SSE receives managed factory |
| `channel.telegram.api` | exact origin | fixed Telegram REST adapter and `getFile` use managed clients |
| `channel.wechat.api` | exact origin | owner-configured iLink origin is frozen and explicitly authorized |
| `channel.feishu.api` | exact origin | fixed registry entry; no active Feishu implementation exists in this checkout |
| `channel.matrix.configured` | exact origin | configured registry entry; no active Matrix implementation exists; real local service compatibility is tested |
| `channel.slack.api` | exact origin | fixed Slack REST and upload negotiation use managed clients |
| `channel.discord.api` | exact origin | fixed Discord POST, PATCH, and multipart REST use managed clients |
| `tts.fixed_api` | exact origin | fixed OpenAI/ElevenLabs REST |
| `tts.configured_api` | exact origin | configured/local TTS with explicit exact-origin authorization |
| `channel.slack.gateway_sdk` | disabled | startup fails closed with `UNMANAGED_TRANSPORT` |
| `channel.discord.gateway_sdk` | disabled | startup fails closed with `UNMANAGED_TRANSPORT` |
| `tts.edge_sdk` | disabled | invocation fails closed with `UNMANAGED_TRANSPORT` |

No active Task 6 entry has an unmanaged disposition. No Task 6 entry uses the
policy-proxy disposition. REST remains active for Slack and Discord; only their
uninjectable gateway SDKs are disabled.

Provider model listing, probes, token counting, token exchange, device code,
PKCE, provider OAuth, Copilot exchange, Google project discovery, Telegram,
WeChat, Slack, Discord, TTS, attachment resolution, and local Web owner probes
were migrated from raw `requests`, `urllib`, or plain `httpx` construction.
The active raw-call audit returns zero matches. `_ports.py` uses
`runtime.local_probe` for exact custom HTTP/HTTPS effective origins; it does not
reuse the semantically unrelated MCP callback key.

## Compatibility and security behavior

The provider client factory requires `consumer` and `configured_origin`, has no
catch-all keyword sink, and rejects unmanaged mounts. It preserves the prior
streaming timeout policy (1860-second read backstop, 7200-second total bound),
TCP keepalive socket options, and the validated IPv4 wildcard escape. These
options enter the managed httpcore pools, so Host/SNI/peer validation is not
bypassed. Environment proxy mounts are not activated because they cannot
enforce target policy; an enforcing proxy must use the unified policy-proxy
configuration.

Shared clients are separated by key, consumer, normalized origin, exact owner
authorization identity, effective timeout, IPv4 setting, socket options, and
event loop. Closed entries are removed before applying a 32-client per-loop
bound; open overflow fails closed. Short-lived loops are explicitly reaped.
Task 2's per-response decision-bound pool means shared client reuse preserves
lifecycle/configuration semantics but does not reuse a connection across
independent URL decisions.

Real tests cover OpenAI, Anthropic, and Google public SDK methods, MCP HTTP and
SSE v1 sessions, local TTS, configured WeChat, configured Matrix, Telegram
REST, and one fixed provider entry through a test-only mapped socket. The
mapped tests replace registry/DNS/socket policy only; they do not replace the
public request methods. The OpenAI SDK redirect test proves a credentialed
cross-origin target is not reached. Error-boundary tests verify peer body,
path, query, token, code, verifier, and exception text are absent from
`SendResult`, stdout, traceback, cause, and context while stable status/error
reasons and retry metadata remain available.

MCP 1.29 uses the nondeprecated `streamable_http_client(http_client=managed)`.
An ImportError-only fallback supports older MCP v1
`streamablehttp_client(httpx_client_factory=managed_factory)`. Both HTTP and
SSE real SDK sessions list and invoke a tool. No deprecation warning remains.

## TDD evidence

- Initial focused RED: `20 failed, 3 passed`.
- Explicit gateway/edge registry RED: `5 failed, 17 deselected`.
- Real provider SDK RED: OpenAI/Anthropic `2 failed`; Google `1 failed`.
- Real MCP v1 SDK RED after lock synchronization: `2 failed, 25 deselected`.
- Anthropic managed-factory fail-open RED: `1 failed, 27 deselected`; GREEN
  with three real provider SDK calls: `4 passed, 24 deselected`.
- Channel secrecy RED: `3 failed, 4 deselected`; GREEN: `3 passed`.
- Custom HTTPS owner-probe RED: `1 failed, 11 deselected`; GREEN with local
  TTS: `2 passed`.
- Local TTS audio MIME RED: `1 failed, 10 deselected`; GREEN after the minimal
  audio response MIME declaration.
- Provider timeout/socket/unknown-kwargs RED: `2 failed, 30 deselected`; GREEN:
  `2 passed`.
- Shared authorization/configuration cache RED: `2 failed`; cap/closed-entry
  RED: `1 failed`; final shared lifecycle set: `7 passed`.
- Compatibility matrix first run exposed two fixed mapped-socket failures;
  final Telegram/provider/WeChat/Matrix set: `4 passed, 12 deselected`.
- Final focused and affected: `271 passed, 2 skipped, 1 xfailed`.
- Task 1-6 security combined: `479 passed`.
- Fresh frozen MCP affected set: `42 passed, 2 skipped`, MCP `1.29.0`.
- The first broad non-integration run exposed eight legacy unit tests that
  still replaced the pre-migration raw `httpx`/`urllib` entry points. Their
  complete node IDs and causes were:
  - `tests/unit/test_anthropic_subscription_login.py::test_refresh_posts_refresh_token`
    replaced `httpx.post` instead of `provider.oauth.fixed`.
  - `tests/unit/test_backend_endpoint.py::test_resolve_backend_endpoint_never_transmits_the_token`
    and `tests/unit/test_backend_identity.py::test_backend_identity_uses_worker_ownership_without_network_credentials`
    replaced `urllib.request.build_opener` instead of the exact-origin
    `runtime.local_probe` configured client.
  - `tests/unit/test_model_fetch_routing.py::test_anthropic_fetcher_uses_provider_base_url`,
    `tests/unit/test_model_fetch_routing.py::test_codex_browse_does_not_grow_registry`,
    `tests/unit/test_model_fetch_routing.py::test_codex_fetch_drops_ultra_and_needs_token`,
    and `tests/unit/test_model_fetch_routing.py::test_anthropic_fetcher_native_still_uses_anthropic_host`
    replaced raw `httpx.get` instead of the fixed/configured provider clients.
  - `tests/unit/test_models_dev_disk_cache.py::test_successful_fetch_writes_disk_cache`
    replaced raw `httpx.get` instead of `webui.model_listing.fixed`.
  Each test now asserts its registry consumer; configured-origin tests also
  assert the exact origin and matching `OwnerURLException`. The eight-node
  targeted run is `8 passed`; all five affected unit files are `40 passed`.
- The same first broad run also failed
  `tests/unit/test_recoverable_delete.py::test_wheel_contains_both_runtime_shims`
  because the frozen environment lacked the external `build` module. The
  identical node fails on a clean BASE archive for the same reason. With the
  pre-fix dev extra present the wheel built, then the unchanged test supplied
  the test-infrastructure RED at `python -m pip`: the uv environment had no
  `pip` module. `pip>=24` was added only to the dev extra and the lock was
  mechanically synchronized; the wheel node is now `1 passed`.

## Verification

- `uv sync --frozen --extra dev`: exit 0 in a fresh temporary environment.
- `uv lock --check`: exit 0 after lock synchronization.
- Brief focused/affected command: `271 passed, 2 skipped, 1 xfailed`.
- `uv run --frozen pytest -q tests/security`: `479 passed in 76.54s`.
- Ruff check over Task 6 production directories and security tests: `All checks passed!`.
- Ruff format applied to and checked for both new Task 6 security test files.
  Whole-directory format check still reports pre-existing BASE formatting debt;
  legacy files were not mechanically reformatted because that would rewrite
  unrelated code.
- `git diff --check`: exit 0.
- Active raw-call audit: zero production matches (the sole textual match is a
  `http_proxy.py` explanatory docstring).
- Post-compatibility focused unit files: `40 passed`; brief affected set:
  `271 passed, 2 skipped, 1 xfailed`.
- Post-compatibility broad non-integration suite:
  `3727 passed, 10 skipped, 1 xfailed, 1 failed`; the only failure is the
  BASE-reproduced wheel-build environment node above. After the dev-extra-only
  test-interpreter repair, the final frozen dev run is
  `3728 passed, 10 skipped, 1 xfailed` in 263.07 seconds. The eight Task 6 seam
  regressions from the first run are green.
- Full integration-inclusive collection remains blocked by the unchanged
  `tests/integration/test_test_framework.py` import of absent module
  `openprogram.functions.agentics.test_framework`: `1 skipped, 2 deselected,
  1 error`. The same missing module was reproduced on the BASE archive.

## Modified files

Production changes cover `openprogram/security/safe_http.py`,
`openprogram/_ports.py`, `openprogram/tts.py`, channel `_transport.py`,
`_attachments.py`, Telegram/WeChat/Slack/Discord implementations,
`openprogram/mcp/client.py`, all brief-listed provider list/probe/auth/OAuth
files, OpenAI/Anthropic/Google/Azure SDK constructors, Google Gemini CLI and
OpenAI Codex streaming call sites, and provider `http_client.py`/
`http_proxy.py`.

Test infrastructure adds `pip>=24` to the `dev` extra in `pyproject.toml` and
synchronizes `uv.lock`; production dependencies are unchanged.

Tests changed or added:

- `tests/security/test_runtime_sdk_transports.py`
- `tests/security/test_runtime_service_consumers.py`
- `tests/security/test_consumer_registry.py`
- `tests/security/test_runtime_derived_url_consumers.py`
- `tests/providers/test_shared_client_leak.py`
- `tests/providers/test_stream_fixes.py`
- `tests/test_http_proxy.py`
- `tests/unit/test_channels_attachments.py`
- `tests/unit/test_anthropic_subscription_login.py`
- `tests/unit/test_backend_endpoint.py`
- `tests/unit/test_backend_identity.py`
- `tests/unit/test_model_fetch_routing.py`
- `tests/unit/test_models_dev_disk_cache.py`

## Concerns

- Slack/Discord gateway SDKs and edge-tts remain intentionally unavailable
  until they expose an enforcing transport or policy proxy; REST/TTS alternatives
  remain explicit and tested.
- Feishu and Matrix have registry dispositions but no active channel adapter in
  this checkout; Matrix configured-origin socket compatibility is tested and
  this absence is not represented as an implemented adapter.
- Legacy whole-file formatting debt remains unchanged outside the two new test
  files.
- The integration-inclusive suite cannot collect because the BASE checkout has
  no `openprogram.functions.agentics.test_framework` module. All non-integration
  tests pass under the frozen dev environment.

## Accepted spec-review fixes

- MCP supervisor sanitization RED:
  `uv run --frozen --extra dev pytest -q tests/security/test_runtime_sdk_transports.py -k 'mcp_supervisor_sanitizes_remote'`
  failed `2` tests because OAuth and transient peer exception bodies reached
  `client.error` and stderr. A shared supervisor renderer now retains only the
  exception class and normalized configured origin for HTTP/SSE while preserving
  local stdio details and `needs_reauth`/`transient` classification. GREEN:
  `2 passed, 32 deselected`.
- Direct short-loop cache RED:
  `uv run --frozen --extra dev pytest -q tests/providers/test_shared_client_leak.py -k 'direct_short_lived_loops'`
  failed on loop 34 with `shared provider client cache limit exceeded`. An
  intermediate cross-loop close implementation was rejected by an owner-loop
  assertion. Each cached loop now owns a cleanup task whose cancellation closes
  and removes that loop's clients during `asyncio.run()` shutdown. Cache keys use
  the loop object rather than a reusable integer id, and the existing per-loop
  cap is supplemented by a process-total cap. GREEN: the focused test passed and
  the complete cache lifecycle file is `8 passed`.
- Post-fix affected suite: `274 passed, 2 skipped, 1 xfailed`.
- Post-fix Task 1-6 security suite: `481 passed in 76.69s`.
- Ruff check: PASS. New security test format check: PASS. `uv lock --check`:
  PASS. `git diff --check`: PASS.

## Spec re-review concurrent-cap fix

- RED command:
  `uv run --frozen --extra dev pytest -q tests/providers/test_shared_client_leak.py -k 'concurrent_loops_reserve_process_capacity_atomically or failed_shared_client_construction_restores_capacity'`
- RED result: `1 failed, 1 passed, 8 deselected`. Forty active
  thread-owned event loops inserted 40 live clients while the process limit was
  32; the expected assertion failed as `assert 40 == 32`. The independent
  construction-failure capacity regression passed before the production change.
- GREEN command: the same two-test command.
- GREEN result: `2 passed, 8 deselected`. A process-global reservation now
  makes capacity check through insertion atomic without holding the lock during
  client construction. Construction failure releases its reservation. Cleanup
  removes the owner loop's entries under the same lock and closes them outside
  the lock on that owner loop. The concurrency regression admits exactly 32
  live clients, rejects eight surplus calls with the stable cache-limit error,
  closes every accepted client on its owner loop, and leaves `_shared` and
  `_loop_cleanup_tasks` empty after shutdown.
- Complete shared-client lifecycle file: `10 passed in 0.09s`.
- Task 6 brief affected set: `276 passed, 2 skipped, 1 xfailed in 11.17s`.
- Complete `tests/security`: `481 passed in 76.70s`.
- Ruff lint: PASS. Ruff format check: both changed Python files formatted.
  `uv lock --check`: PASS. `git diff --check`: PASS.
