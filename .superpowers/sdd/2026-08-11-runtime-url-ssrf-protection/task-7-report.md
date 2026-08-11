# Task 7 Report: Owner URL Security, Audit, Doctor, and Static Enforcement

## Status and baseline

Implementation and first spec-review fixes complete pending independent
re-review. Baseline:
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
| `functions/tools/browser/_chrome_bootstrap.py` | Browser control | two urllib opener constructions and one loopback CDP liveness dial |
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
- Spec review fix RED, inventory group: `4 failed, 10 deselected`. The failures
  proved that an unrelated string argument could claim a consumer, a same-file
  managed consumer could hide a naked SDK constructor, `socket.create_connection`
  was absent, and boundary exclusions did not bind every kind/count. GREEN:
  `4 passed, 10 deselected`.
- Adjacent fail-closed inventory RED: a lookalike `unrelated.safe_client` and
  an injected SDK constructor whose exact consumer was declared `DISABLED`
  both passed. GREEN: `2 passed, 14 deselected`; managed factory symbols and
  SDK dispositions now require exact recognized associations.
- Live owner-policy cache RED: `2 failed, 10 deselected`; changing policy proxy
  `3111` to `3222` and revoking a persisted exception both reused the old
  provider client. GREEN: `2 passed, 10 deselected`; the cache key now includes
  the effective immutable policy snapshot and retires superseded clients.

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

Spec-review fix verification:

- Task 7 focused plus provider cache lifecycle: `69 passed in 1.49s`.
- Static checker: `unregistered=0 active_unmanaged=0
  registry_without_consumer=0 stale_exclusions=0`.
- `tests/security`: `531 passed in 77.18s`.
- Provider/config affected suite: `131 passed, 1 xfailed in 0.48s`.
- Full frozen dev non-integration suite: `3789 passed, 10 skipped, 1
  xfailed, 4 warnings in 273.06s`.
- Ruff lint, scoped Ruff format, `uv lock --check`, and `git diff --check`:
  passed.

Second spec re-review fix:

- Independent re-review closed manifest/socket and live-policy cache findings,
  but found that any non-`None` SDK injection argument still counted as
  managed. Four public scanner probes covered an arbitrary object, an exact
  factory with the wrong consumer, unrelated same-file consumer evidence, and
  an `unrelated.safe_http.safe_client` lookalike. RED: `4 failed, 1 passed, 16
  deselected`.
- The scanner now recognizes only exact approved managed factory identities,
  propagates their literal consumer through direct values and the supported
  assignment/container form, and requires that provenance to equal the SDK
  constructor's immutable registry consumer. Exact in-repository Google,
  Anthropic, and MCP helper paths remain classified without accepting dotted
  suffix lookalikes.
- Targeted GREEN: `5 passed, 16 deselected`. Inventory plus shared-cache
  lifecycle: `33 passed in 1.15s`. Repository checker again reported all four
  categories as zero.
- Final second-fix focused command: `74 passed in 1.50s`; complete
  `tests/security`: `536 passed in 77.32s`.
- Same-finding provenance self-review added a selected-tuple expression and a
  managed-then-unmanaged reassignment. RED: `2 failed, 4 passed, 17
  deselected`. The analysis now follows only structurally selected values and
  clears provenance on unmanaged reassignment instead of accepting any nested
  factory descendant or retaining stale assignment state. GREEN: `7 passed,
  16 deselected`; checker categories remain all zero. Final focused: `76
  passed in 1.40s`; complete `tests/security`: `538 passed in 77.78s`; Ruff,
  format, lock, and diff checks passed.

Quality-review control-flow fix:

- The independent quality review found that managed-value provenance was
  updated in AST traversal order, so a later managed branch could hide an
  unmanaged reachable exit. Eight negative cases cover both branch orders for
  `if`, `try`, and `match`, plus zero-iteration and interrupted loop exits;
  four positive cases require the same exact consumer on every analyzed exit.
  RED: `5 failed, 7 passed, 23 deselected`.
- `If`, `Try`/`TryStar`, `For`/`AsyncFor`/`While`, and `Match` now analyze each
  alternative from the same incoming state and retain provenance only when
  every possible continuing state has the same exact consumer. GREEN: `12
  passed, 23 deselected`.
- Final focused Task 7 and cache lifecycle suite: `88 passed in 1.59s`.
  Static checker: `unregistered=0 active_unmanaged=0
  registry_without_consumer=0 stale_exclusions=0`. Complete
  `tests/security`: `550 passed in 77.55s`. Ruff lint, scoped Ruff format,
  `uv lock --check`, and `git diff --check` passed.

Second quality re-review fix:

- Scoped re-review found seven remaining fail-open paths: intermediate
  `Try`/`TryStar` exception states, `For`/`AsyncFor` target assignment,
  unreachable managed assignments after `continue`/`break`, and `Match`
  capture binding. The exact seven public scanner probes all failed before the
  production change: `7 failed, 35 deselected`.
- Block analysis now separates normal, break, continue, return, and raise
  outcomes. Try handlers receive the conservative intersection of every
  per-statement exception checkpoint; iteration targets and match captures
  clear any previous provenance before their body; terminated blocks do not
  inspect unreachable assignments. All seven regressions and the prior branch
  tests pass: `19 passed, 23 deselected`.
- Adjacent TDD added three flow-integrity cases. A `finally` assignment on a
  break exit failed `1 failed, 7 passed`; a nested conditional raise omitted
  its exceptional state and failed `1 failed, 8 passed`; a return in a nested
  function incorrectly terminated its enclosing loop analysis and failed `1
  failed, 9 passed`. Finally now transforms every normal and non-normal
  outcome, nested raises join handler checkpoints, and function/class scopes
  isolate their flow collectors. The ten hidden-state negatives pass.
- Five positive regressions verify that non-continuing return/raise paths are
  excluded while handler restoration, loop break/else, and match-return paths
  retain an exact consumer when every actual continuing exit agrees. Complete
  control-flow set: `27 passed, 23 deselected`.
- Final Task 7 focused and cache lifecycle suite: `103 passed in 1.47s`.
  Static checker again reported all four categories as zero. Complete
  `tests/security`: `565 passed in 77.64s`. Ruff lint and scoped format passed;
  final lock and diff checks are recorded with the fix commit below.

Third quality re-review fix:

- The second scoped re-review closed the original seven probes but found a
  guarded capture fallback that retained pre-match provenance, plus return and
  raise paths being merged with the only normal path before `finally`. The
  exact three public regressions produced `3 failed, 15 passed, 35 deselected`.
- Guard-false match processing now advances with the capture-bound state.
  Finally analysis preserves flow-kind association, applies the final block to
  each distinct incoming state, merges only states of the same outgoing kind,
  and intersects duplicate SDK-call provenance while avoiding duplicate raw
  call inventory entries. Targeted GREEN: `18 passed, 35 deselected`; complete
  control-flow set: `30 passed, 23 deselected`.
- Final inventory file: `53 passed in 1.13s`; Task 7 focused and cache
  lifecycle suite: `106 passed in 1.43s`; static checker categories all zero;
  complete `tests/security`: `568 passed in 77.30s`. Ruff lint, scoped format,
  lock, and diff checks passed.

Fourth quality re-review fix:

- The third scoped re-review found that a refutable Match pattern miss reused
  capture-killed provenance, so a managed pre-pattern value and managed case
  body were incorrectly classified unmanaged. The exact public scanner
  regression failed `1 failed`; preserving pattern-miss pre-state separately
  from pattern-hit/guard-false bound state passed `1 passed`. The adjacent
  managed-continuing and guarded-capture set passed `19 passed, 35
  deselected`.
- The review also found function roots still used generic AST traversal, so
  terminating `finally: raise` and `finally: return` blocks did not suppress
  unreachable SDK constructors. The two exact public scanner regressions
  failed `2 failed`; function and async-function roots now scan headers and
  analyze bodies through an isolated block-flow collector. Exact GREEN was `2
  passed`; async termination plus reachable SDK and nested function/class
  isolation passed `4 passed, 54 deselected`.
- The complete inventory file passed `58 passed in 1.17s`; Task 7 focused and
  shared-client cache lifecycle passed `111 passed in 1.46s`.
- Final static checker reported `unregistered=0 active_unmanaged=0
  registry_without_consumer=0 stale_exclusions=0`; complete `tests/security`
  passed `573 passed in 78.16s`. Required Ruff lint passed. Ruff formatted the
  new adjacent assertion, after which scoped format check, the inventory file
  (`58 passed in 1.13s`), `uv lock --check`, and `git diff --check` passed.
  Fresh pre-commit focused and shared-cache verification passed `111 passed in
  1.39s`; the checker again reported all four categories as zero.

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
