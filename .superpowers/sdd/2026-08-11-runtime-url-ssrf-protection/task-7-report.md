# Task 7 Report: Owner URL Security, Audit, Doctor, and Static Enforcement

## Status and baseline

Implementation complete pending independent reviews. Baseline:
`23f40cf8e81cbd822d39e3a3a380c432b251b6f8`.

## Implementation scope

- Added strict, frozen Pydantic owner configuration at
  `security.outbound_url`, bounded to 64 unique exceptions. Exceptions are
  scoped to an immutable registry consumer, contain exactly one normalized
  Origin or private/local CIDR, and reject unknown fields, wildcard/suffix
  hosts, credentials, path/query/fragment, public/default-route CIDRs,
  metadata and link-local targets. Policy proxy configuration requires a
  strict boolean target-policy enforcement assertion and an exact sanitized
  proxy Origin.
- Reused `setup.update_config` for atomic 0600 persistence. Existing mandatory
  Web HTTP/WebSocket owner middleware remains the only mutation authority;
  public-path tests prove unauthenticated writes are rejected before the
  settings handler. No new write route or authority mechanism was added.
- Managed sync/async factories load the immutable owner configuration when no
  explicit test security object is supplied. Configured-service factory
  authorization merges exact call-site owner provenance with persisted
  consumer-scoped exceptions. A configured policy proxy synthesizes only the
  exact `runtime.local_probe` exception required to constrain its peer.
- Added a process-wide 256-event denial ring. It stores only a registered
  consumer, bounded reason code, sanitized Origin, proxy-delegation boolean,
  and UTC timestamp. Peer-controlled reason/consumer/path/query/userinfo are
  never retained. Per-client audit compatibility remains unchanged.
- Added a shared AST inventory and CI script. It detects urllib, requests,
  httpx, httpcore, aiohttp, urllib3, raw socket connects, and known SDK
  constructors; records exact-kind boundary exclusions with owner/reason;
  rejects stale exclusions; compares detected or explicitly declared
  consumers with the immutable registry; and reports active unmanaged SDKs.
- Added five stable doctor labels: `runtime-http-registry`,
  `runtime-http-owner-exceptions`, `runtime-http-policy-proxy`,
  `runtime-http-recent-denials`, and `runtime-http-unmanaged-transport`.
  Invalid persisted configuration and inventory gaps fail closed; all details
  are Origin-only or CIDR-only.

## Boundary declarations

| Path | Owner | Narrow excluded call |
|---|---|---|
| `security/safe_http.py` | Runtime managed transport | httpcore pools/proxies only |
| `functions/tools/browser/_chrome_bootstrap.py` | Browser control | urllib opener construction only |
| `_cli_cmds/mcp.py` | Owner control plane | authenticated backend urllib opener only |
| `cli_ink.py` | Owner control plane | loopback worker socket liveness only |
| `_cli_cmds/doctor.py` | Owner control plane | loopback worker socket liveness only |
| `_cli_cmds/rescue.py` | Owner control plane | loopback worker socket liveness only |

An exclusion does not suppress other call kinds in the same file. Missing
files or declarations whose exact call disappeared are reported as stale.
Feishu/Matrix absent adapters, the legacy generated-asset key, channel-selected
attachment keys, and dynamically selected MCP/image keys have explicit
declaration reasons rather than false detected call sites.

## TDD evidence

- Initial owner parser/write RED:
  `uv run --frozen --extra dev pytest -q tests/security/test_owner_url_exceptions.py tests/unit/test_web_config_schema.py tests/unit/test_config_write_safety.py`
  -> `23 failed, 5 passed`; first GREEN -> `28 passed`.
- Managed factory owner-policy RED -> `1 failed, 1 passed`; GREEN with the
  explicit-test-security isolation test -> `2 passed`.
- Audit/doctor/inventory module RED: approved five-file command stopped with
  two expected import collection errors because `runtime_http_audit` did not
  exist. First GREEN -> `38 passed`; inventory CLI reported all four zero.
- Metadata Origin, strict proxy boolean, raw chained socket, and owner
  WebSocket additions initially produced six failures; three were production
  gaps and three were corrected test expectations/fixtures. GREEN ->
  `35 passed` for owner and inventory tests.
- Exact docstring-only registry evidence RED -> `1 failed`; call-site evidence
  tracking GREEN with repository inventory zero.
- Peer-controlled shared-audit fields RED -> `1 failed`; bounded reason and
  registered-consumer projection GREEN -> `1 passed`.
- Exception capacity/duplicate RED -> `1 failed`; GREEN -> `1 passed`.
- Public/multicast CIDR RED -> `2 failed, 20 passed`; GREEN after private/local
  restriction.
- Malformed JSON reflection RED -> `1 failed`; GREEN uses the stable sanitized
  setting error.
- Persisted non-object `security` RED -> `1 failed`; GREEN fails closed rather
  than treating it as empty policy.

## Final verification evidence

- Approved focused command: `51 passed in 1.31s`.
- `uv run --frozen --extra dev python scripts/check_runtime_http.py`:
  `unregistered=0 active_unmanaged=0 registry_without_consumer=0 stale_exclusions=0`.
- `uv run --frozen --extra dev pytest -q tests/security`: `525 passed in
  77.96s`.
- Affected provider/config/owner/commitment set: `248 passed in 4.87s`.
- Full frozen dev non-integration unit suite: `3781 passed, 10 skipped, 1
  xfailed, 4 warnings in 274.57s`.
- Required Ruff scope: `All checks passed!`.
- New Task 7 Python files were formatted with Ruff. Whole-file format checks
  for the legacy `config_schema.py` and `doctor.py` remain unchanged from the
  baseline; no unrelated mechanical reformat was applied.
- `git diff --check`: passed.

## Concerns

- Browser navigation, arbitrary-code networking, and owner control-plane
  liveness remain separate trust boundaries as approved; the static manifest
  classifies them but does not route them through Runtime URL fetch policy.
- Four full-suite warnings are dependency deprecations from the real WebSocket
  owner-auth listener tests; there are no Task 7 test failures.

## Commit

The Task 7 implementation, tests, and this report are committed together as
`feat(security): audit runtime HTTP enforcement`; use the repository commit ID
rather than embedding a self-referential pre-amend SHA here.
