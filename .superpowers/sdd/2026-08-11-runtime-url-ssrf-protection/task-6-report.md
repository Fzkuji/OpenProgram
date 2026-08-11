# Task 6 Report: Runtime Service and SDK HTTP Transports

## Status and commits

Complete. Baseline: `26cf395c78bd623ded1763645164e25cfc2bcd98`.

- Frozen MCP v1 lock synchronization: `12f9a1c4`
- Task 6 implementation and this report: this commit

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

## Modified files

Production changes cover `openprogram/security/safe_http.py`,
`openprogram/_ports.py`, `openprogram/tts.py`, channel `_transport.py`,
`_attachments.py`, Telegram/WeChat/Slack/Discord implementations,
`openprogram/mcp/client.py`, all brief-listed provider list/probe/auth/OAuth
files, OpenAI/Anthropic/Google/Azure SDK constructors, Google Gemini CLI and
OpenAI Codex streaming call sites, and provider `http_client.py`/
`http_proxy.py`.

Tests changed or added:

- `tests/security/test_runtime_sdk_transports.py`
- `tests/security/test_runtime_service_consumers.py`
- `tests/security/test_consumer_registry.py`
- `tests/security/test_runtime_derived_url_consumers.py`
- `tests/providers/test_shared_client_leak.py`
- `tests/providers/test_stream_fixes.py`
- `tests/test_http_proxy.py`
- `tests/unit/test_channels_attachments.py`

## Concerns

- Slack/Discord gateway SDKs and edge-tts remain intentionally unavailable
  until they expose an enforcing transport or policy proxy; REST/TTS alternatives
  remain explicit and tested.
- Feishu and Matrix have registry dispositions but no active channel adapter in
  this checkout; Matrix configured-origin socket compatibility is tested and
  this absence is not represented as an implemented adapter.
- Legacy whole-file formatting debt remains unchanged outside the two new test
  files.
