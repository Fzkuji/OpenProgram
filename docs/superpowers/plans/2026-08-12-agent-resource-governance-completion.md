# Agent Resource Governance Completion Plan

> Implement task by task with strict TDD. Each task requires an independent spec review and quality review before the next task starts.

**Goal:** Complete the approved resource-governance design on current `main`, including fair durable scheduling, crash-safe ownership, enforceable token/cost/runtime/idle budgets, and one user-visible DTO across all supported surfaces.

**Baseline:** `main@990bfe36`. The foundation from `8f62da5a` is present. The branch ending at `ace9a907` is candidate code only: it may be reused after rebase and review, but it is not implementation evidence. Mixed historical integration branches must not be merged wholesale.

**Architecture:** Keep `ResourceGovernor` and the usage SQLite database as the durable authority. `TaskRunner` owns one dispatcher and submits only claimed tasks. A task-scoped ContextVar exposes an immutable governance handle to the existing provider `stream()/stream_simple()` chokepoint. Budgeted calls reserve before credential/network work, clamp the output/deadline, then atomically settle the existing append-only `UsageEvent`. Web, CLI, TUI, and model tools serialize the same `TaskResourceView`; they do not recompute limits independently.

## Fixed constraints

- Keep the single-user, local, single execution-worker topology and `OPENPROGRAM_TASK_WORKERS` as the only global capacity.
- Do not add a second usage ledger or edit historical usage events.
- Missing legacy fields mean `legacy/unmetered`, never zero.
- Queue, live, stopping, cumulative, actual usage, and unsettled reservations remain distinct.
- Unknown price is not zero. A configured cost budget rejects a call unless pricing provenance and every required rate are known.
- A budgeted provider call is not best-effort accounting: reserve/settle failure must return a stable typed quota/accounting error.
- Preserve existing Task ids/status values, cancellation API, broadcasts, depth/fanout/message limits, and unbudgeted legacy behavior.
- Do not claim immediate thread termination. `stopping` retains live capacity until the worker confirms exit.
- Do not mark the feature matrix solid until every gate in the HTML design has current code and automated evidence.

## Task 1: Rebase and validate the durable dispatcher and ownership fixes

**Primary files:**

- `openprogram/agent/resource_governance.py`
- `openprogram/agent/task/runner.py`
- `openprogram/agent/task/store.py`
- `tests/unit/test_resource_governance.py`
- `tests/unit/test_async_task.py`

Use the behavior from `31f62624`, `b068dbfa`, `ace9a907` and the later resource-only fixes corresponding to owner fencing, serialized turn admission, busy-claim release, atomic stopping finalization, and failed-finalization recovery. Reimplement or cherry-pick only after confirming the diff applies to current main without MCP/JSON/recording dependencies.

**RED requirements:**

- A blocked session with enough queued tasks to exceed `max_workers` cannot prevent an eligible second session from starting.
- Only the oldest eligible durable admission is claimed; queue wait never consumes live.
- Two runners/processes cannot claim the same task or mutate another owner's live/stopping row.
- Cancellation, watchdog, busy-target withdrawal, worker crash, lease expiry, and finalization failure never release a live claim before execution exits and never leak it permanently.
- Dispatcher restart and repeated reconciliation are idempotent.

**GREEN boundary:** `spawn_task()` persists and wakes the dispatcher but never submits unclaimed work. The dispatcher atomically claims eligible work and only then submits the executor job. All live mutations require the matching owner id.

## Task 2: Establish task-scoped budget attribution and preflight estimation

**Primary files:**

- `openprogram/agent/resource_governance.py`
- `openprogram/agent/task/runner.py`
- `openprogram/usage/context.py`
- `openprogram/usage/event.py`
- `openprogram/providers/types.py`
- `openprogram/context/tokens.py`
- new focused budget-context tests

Add one immutable task-governance context containing `task_id`, `budget_scope_id`, governor/ledger identity, effective limits, and monotonic deadline/activity callbacks. Set/reset it only around the claimed task body; child contexts inherit through normal ContextVar propagation.

Define one conservative provider preflight function:

- input upper bound includes rendered system prompt, messages, tools, structured-output schema/instructions, provider wrapper overhead, and cache-write exposure;
- output upper bound is the smaller positive value from request cap, model cap, and remaining budget;
- if a strict token budget lacks a safe upper bound, reject with `quota.accounting_unavailable` before credentials or network;
- model pricing has explicit known/unknown provenance; default numeric zeros cannot establish known pricing;
- a cost budget requires known input/output/cache rates and computes a micro-USD upper bound without floating-point comparison.

**RED requirements:** nested/parallel task context isolation, no attribution outside a governed task, schema/tool/image overhead, missing tokenizer/price, explicit free-price metadata, and no credential resolver/network call after denied preflight.

## Task 3: Integrate token and cost reserve/start/settle at the provider chokepoint

**Primary files:**

- `openprogram/providers/stream.py`
- `openprogram/usage/recorder.py`
- `openprogram/usage/ledger.py`
- `openprogram/agent/resource_governance.py`
- provider stream/usage tests

Before invoking any registered provider, copy options and apply the preflight output cap. In one short transaction, reserve token exposure and cost exposure across the task and all ancestor/session scopes. Mark reservations started immediately before provider I/O.

At the terminal event, build one `UsageEvent` with `task_id`, `budget_scope_id`, and reservation ids, append it exactly once, atomically settle actual provider-authoritative usage, and release only the unused exposure. Cache read/write counters stay separate and are not added twice to `total_tokens`.

Failure semantics:

- provider refuses before request start: release reservations;
- request may have reached provider but no final usage: keep conservative unsettled exposure;
- late terminal usage settles the same reservation idempotently;
- ledger/settlement failure in a budgeted call is a typed accounting failure, not swallowed by the existing best-effort recorder;
- unbudgeted calls retain current best-effort recording behavior.

**RED requirements:** sibling races, ancestor limits, retry/failover attempts, missing/late usage, terminal error events, consumer cancellation, double terminal events, settlement DB failure, unknown cost, output-cap propagation through real OpenAI/Anthropic/Google adapters, and legacy calls.

## Task 4: Complete runtime, idle, deadline, and bounded-operation enforcement

**Primary files:**

- `openprogram/agent/resource_governance.py`
- `openprogram/agent/task/runner.py`
- `openprogram/providers/utils/deadline.py`
- `openprogram/functions/_runtime.py`
- tool progress and child-task update boundaries
- runtime/idle focused tests

Reuse the reviewed parts of `ace9a907`, after Task 1 ownership fixes. Runtime begins only after durable claim. Meaningful activity is limited to parsed provider data, tool progress, child progress/terminal events, and explicit operation boundaries; transport keepalive and polling do not reset idle.

Every provider/tool operation receives the minimum positive bound from its own timeout, remaining task runtime, remaining idle budget where applicable, and system cancel grace. A strict-budget task rejects an in-process synchronous operation without a declared enforceable bound using `error.nonpreemptible_operation`.

Expiry records `budget.runtime_exhausted` or `budget.idle_exhausted`, invokes the existing descendant cancellation path, keeps the admission in stopping, and releases only from confirmed worker finalization.

**RED requirements:** queue time exclusion, active stream versus keepalive, tool and child activity, nested deadline restoration, concurrent expiry/cancel, an executor thread surviving timeout, subprocess termination, stopping capacity retention, and wall-clock diagnostics after restart.

## Task 5: Expose one resource DTO through Web, CLI, TUI, and model tools

**Primary files:**

- `openprogram/agent/resource_governance.py`
- existing task list/get/cancel model tools
- existing Web task actions/components
- CLI/TUI task/status command paths
- contract tests for each surface

Complete `TaskResourceView` as the only serializer. It includes scheduler capacity; configured/effective/source limits; queue position; resource state; live/queued/cumulative usage; actual/reserved token and cost; unknown-cost count; runtime/idle used and limit; shared remaining; stable reason code; retryable; and legacy state.

Surfaces may omit presentation-only labels, but their machine fields and null/unknown semantics must match. Owner-only mutation uses the existing configuration authority path. No surface may infer `$0`, unlimited, or available capacity from missing data.

**RED requirements:** identical DTO fixtures across all four surfaces, legacy tasks, unknown cost, queued/live/stopping/terminal states, session override changes, lowered limits, stale revision/auth failure, and accessibility for visible status controls.

## Task 6: Crash/concurrency release gate and documentation evidence

Run failure injection at every admission/reservation/finalization transaction boundary, 20+ thread/process contention, worker restart, lease expiry, late usage, and settlement retry. Then run focused, affected, full unit, Web lint/build, CLI type/tests, Ruff on changed files, docs build/link checks, and `git diff --check`.

Update `docs/reference/design/runtime/agent-resource-governance.html` with exact commits, commands, counts, and remaining boundaries. Update the feature matrix only after its seven-item mechanical gate passes. If token/cost/runtime/idle later become separate matrix rows, grade each against its own complete gate rather than inheriting the concurrency result.

## Review order

1. Task 1 spec review: scheduler eligibility, ownership, crash recovery, no executor queue bypass.
2. Task 1 quality review: lock ordering, transaction duration, shutdown, test determinism.
3. Tasks 2–3 each receive separate spec and quality reviews because they are billing/security boundaries.
4. Task 4 review includes real provider/tool/subprocess chains, not helper-only tests.
5. Task 5 review compares serialized outputs from all surfaces.
6. Task 6 final review starts from the approved HTML and verifies every implementation claim against current code and fresh command output.
