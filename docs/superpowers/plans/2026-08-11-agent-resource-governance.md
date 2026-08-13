# Agent resource governance implementation plan

Specification: `docs/reference/design/runtime/agent-resource-governance.html`.

## Global constraints

- Preserve the single-user, local, single execution-worker topology. `OPENPROGRAM_JOB_WORKERS` remains the only global scheduler capacity; do not add `max_live_global`.
- Preserve existing Task ids/status enums, depth, fanout, message-budget semantics, broadcasts, cancellation APIs, and legacy tasks. Missing resource fields mean `legacy/unmetered`, never zero.
- Follow strict TDD and record RED/GREEN evidence. All creation paths must use one admission boundary; rejected admissions create no Task, branch, inbox, or event side effects.
- Use SQLite WAL and `BEGIN IMMEDIATE` for admission/reservation atomicity. Never hold a SQLite transaction across jobs.json file I/O. Durable `preparing` is committed before Task publication, and every concurrent check counts its provisional occupancy.
- Queue, live, stopping, cumulative tasks, actual usage, and unsettled reservations are distinct. Actual provider usage remains append-only in `usage_events`.
- New configuration values are positive or null. Task/child limits can only narrow owner/session/ancestor limits. Unknown cost is never treated as zero.
- Do not mark the matrix row solid until the mechanical gate in the specification passes.

## Task 1: Configuration, schema migration, and read-only resource views

Add `agent.resource_limits`, session overrides, configured/effective/source resolution, SQLite migrations for admissions/scopes/reservations and nullable task attribution on usage events, legacy handling, unknown-cost aggregation, and read-only `JobResourceView`. Money is decimal-string at APIs and integer micro-USD in storage.

Required verification: schema upgrade/reopen/rollback compatibility, invalid/zero values, owner-only mutations, child narrowing, worker-capacity projection, unknown-cost display, legacy tasks, and existing usage queries.

## Task 2: Unified durable admission, queue, dispatcher, and recovery

Route sync/async agent, busy-target, Web/API, model tools, and runner calls through `ResourceGovernor.admit_task`. Commit a provisional `preparing` row in the first transaction; write Task under cross-process file lock; finalize `queued` in a second transaction; create branch/inbox and broadcast only afterward. Add durable dispatcher fairness, atomic queued-to-live exchange, per-session live/queued/cumulative limits, stopping leases, idempotent retries, reconciliation, and stable reason codes. Renew leases every 10 seconds with a 30-second TTL; never release while the original owner still holds the worker lock.

Required verification: every entry point, 20+ threads/processes, exact boundary counts, failure at every transaction/file/event point, idempotent retry/conflict, queue fairness, lowered limits, watchdog with live thread, worker death/lease expiry, and no permanent leak or oversubscription.

## Task 3: Token and cost scopes with reserve/settle

Add session/task budget scopes, ancestor narrowing, shared remaining values, provider request attribution, conservative token/cost reservations, provider output-cap clamping, settlement into existing usage events, unknown-price fail-closed behavior when a cost budget exists, and durable unsettled exposure after ambiguous completion.

Required verification: siblings racing for shared budget, safe token upper bounds, cache token treatment, known/unknown prices, late/missing usage, settlement DB failure, recovery of reserved/started states, and non-budget legacy recorder compatibility.

## Task 4: Runtime and idle enforcement

Start runtime accounting at live, exclude queue time, define meaningful activity events, clamp provider/tool deadlines, add active timers and cascade cancellation. Keep live capacity in stopping until execution exits. Strict-budget tasks reject synchronous in-process operations that cannot guarantee exit within the minimum of operation timeout, remaining runtime, and cancel grace.

Required verification: provider data versus keepalive, tool/child progress, runtime and idle boundaries, deadline clamping, non-preemptible tool rejection, cancellation cascade, executor thread surviving wait timeout, and lease release only after confirmed exit.

## Task 5: Web, TUI, CLI, model tools, and final gate

Expose the same DTO, reason codes, retryable flag, queue position, configured/effective/source limits, capacity, shared remaining budget, unknown cost, and legacy state across all surfaces. Reuse current task cards/lists/query/cancel commands. Run focused, affected, full unit, concurrency/crash, Web/CLI type/build, docs-link, lint, and matrix-mechanical checks. Update implementation evidence only for proven gates.
