# Usage Metering Subsystem Design

## 1. Goals

**Every LLM call** in the framework has its tokens, model, and cost recorded with no gaps, carrying source labels, time series, and aggregation by model / source / session. This supports the visualization panel and cost accounting, and leaves extension points for quotas, rate limiting, budget alerts, and export.

Accounting keeps no compatibility path for cumulative snapshot-style records; a responsibility that should be split or deleted is split or deleted.

## 2. What the Accounting Must Cover

Every LLM call ultimately goes through `providers/stream.py`'s `stream_simple()` (streaming) / `complete_simple()` (non-streaming, which runs stream_simple internally). The returned `AssistantMessage` carries `usage` (a `Usage` object: input/output/cache_read/cache_write/total_tokens/cost). That makes `stream.py` the one place where a complete record of a call is available, so it is where metering collects.

Two pre-existing accounting layers stay in place and answer different questions:

- Message level: `dispatcher/persistence.py` writes tokens into the assistant message's history columns — the data behind the per-message pill.
- Compaction budget: `context/usage.py`'s `UsageTracker.record_turn()` writes into the git session meta's `_usage`, the state a compaction decision reads.

Neither is a billing account. The session meta `_usage` is a cumulative snapshot: it cannot be aggregated across sessions, cannot be queried by time bucket, and carries no per-model, per-source, or cost breakdown. Metering therefore adds a third, separate record — an append-only event stream — rather than extending either.

Three properties decide the design:

1. **One collection point.** Accounting sits inside `stream.py` itself, not in a caller one layer up. A path that reaches a provider by another route (as `memory/llm_bridge.py` once did by calling `api_provider.stream_simple()` directly) is routed back through `stream.py` instead of being metered separately, so "went through stream_simple" and "was accounted for" mean the same thing.

2. **Events, not snapshots.** One row per call, append-only, so any aggregation is a query rather than a schema change.

3. **Separated responsibilities.** Billing accounting, compaction threshold estimation, and hot-path budget caching have different lifecycles and consumers, and live in different objects.

Paths that reach the provider outside the chat loop — `context/summarize.py`, `functions/tools/mixture_of_agents`, `memory/llm_bridge.py`, and `@agentic_function` subprocesses (`process_runner.py`) — are all covered by the same collection point plus an explicit source scope.

`providers/models.py:calculate_cost(model, usage)` already computes cost from `Model.cost`. The metering layer calls it at the collection point; no new pricing logic exists.

## 3. Layered Architecture

```
Consumer layer   webui panel / CLI / export / future quota engine
          │ query(filters, group_by, time_bucket)
Storage layer    UsageLedger  —  single SQLite DB, usage_events table (append-only)
          │ record(UsageEvent)
Accounting layer UsageRecorder  —  the single collection point: usage + model + source context → UsageEvent
          │ reads call-context
Context layer    UsageContext  —  contextvar + usage_scope() context manager

(kept separate) context budget estimation — compaction threshold, split out of UsageTracker
```

New module `openprogram/metering/`:
- `event.py` — `UsageEvent` schema
- `context.py` — contextvar + `usage_scope()` / `current_usage_context()` / `snapshot()` / `apply_snapshot()`
- `ledger.py` — `UsageLedger` (SQLite backend + aggregation queries)
- `recorder.py` — `UsageRecorder` (collection point, best-effort)
- `__init__.py` — facade

Placed at the top level as `metering/` rather than under `context/`: metering is a cross-cutting concern (providers/agent/memory/functions all depend on it), and putting it under context would create a reverse `providers → context` dependency. `metering/` depends only on `providers/types` (pure data), with no cycle.

## 4. UsageEvent Schema

One event = the complete accounting record of one LLM call (`metering/event.py`, pydantic frozen):

Identity: `event_id` (uuid idempotency key), `ts` (unix epoch float).
Attribution: `session_id`, `parent_session_id` (subagent attributed to parent), `agent_id`, `call_kind` (the core source label), `call_label` (free-text refinement), `origin_pid` (main process vs subprocess).
Model: `provider`, `api`, `model_id`.
tokens (provider's authoritative values, 0 when missing): `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `total_tokens`.
cost (USD, flattened for easy SUM): `cost_input/output/cache_read/cache_write/total`, `cost_source` ("model_catalog"|"provider_reported"|"unknown").
Provenance: `token_source` ("provider_usage"|"anthropic_count_api"|"estimate"), `schema_version`.

`call_kind` uses a string rather than an Enum (extensible — adding a new caller does not touch the underlying layer):
`chat` / `exec` / `compaction` / `summarize` / `memory` / `subagent` / `tool` / `title` / `unknown`.

Tradeoff: cost is flattened rather than a nested UsageCost → SQLite columnization, SUM needs no JSON parsing; token_source as a single column → the panel can mark a row as "estimated" to avoid misleading cost figures.

## 5. Propagating the Source Label

The underlying `stream_simple` does not know who called it. Three ways to pass it:

| Approach | Pro | Con |
|---|---|---|
| Explicit parameter `options.call_kind` | Explicit | Every caller has to change, threads through many layers of signatures, violates "adding a caller doesn't touch the underlying layer" |
| `SimpleStreamOptions.metadata` | Field already exists | Easy to miss in deep calls; also doesn't reach when memory bypasses stream.py |
| **contextvar** | One line `with usage_scope(...)`, async Tasks inherit automatically | Not propagated automatically across processes/threads (needs an explicit snapshot) |

The contextvar is primary, with an explicit metadata override as fallback. `metering/context.py`:
`usage_scope(call_kind, call_label, parent_session_id, agent_id)` context manager, set/reset the contextvar, supports nested merge. `current_usage_context()` reads it. `snapshot()`/`apply_snapshot()` serialize across processes.

Boundary notes: asyncio Tasks created by default use `copy_context()`, so stream_simple downstream of create_task can read the correct scope. Thread boundaries (run_in_executor/raw Thread) do not inherit → call apply_snapshot at the entry point. The process fork boundary copies the contextvar's current value (favorable for process_runner fork), but spawn does not → snapshot/apply_snapshot as the reliable path.

recorder merge priority: `metadata.usage` > contextvar > default `unknown`.

## 6. Collection Point

An accounting decorator wraps `stream.py`'s `stream()`/`stream_simple()`, and `memory/llm_bridge.py` goes through stream.py rather than around it.

Why here:
1. stream.py is the semantic boundary of "one logical LLM call", already does api_key resolution / provider lookup, and can obtain the model (with cost) + the final AssistantMessage.usage.
2. stream() is a generator, so the wrapping approach = when consuming done/error, extract final_message.usage → `calculate_cost` → read the contextvar → assemble a UsageEvent → recorder. Streaming is not blocked; accounting fires exactly once on the terminal event.
3. Not the api_registry layer: ApiProvider is a Protocol, each implementation has its own stream_simple, so collecting there means either changing the Protocol (invasive to every provider) or wrapping the registry (scattered wrap points). stream.py is the single-function collection point.
4. memory must be pulled back: currently `llm_bridge.py` connects directly to api_provider = unaccounted. Change it to call `providers.stream_simple` + `usage_scope("memory")`, which incidentally fixes the header inconsistency that a comment in stream.py worried about.

Invariant: accounting failures must be best-effort (swallowed with try/except), and must never interrupt the LLM response.

The dispatcher's existing `persist_assistant_message` (the messages columns) is **left untouched** — that is the data for the per-message pill. The ledger is a separate, second authoritative account: the message columns = "how much did this message cost", the ledger = "an aggregatable global stream".

## 7. Storage: a standalone global SQLite ledger

The ledger is a standalone `~/.openprogram/usage.db`, not a cumulative dict in session meta. It holds a single append-only table:

```sql
CREATE TABLE usage_events (
    event_id TEXT PRIMARY KEY, ts REAL NOT NULL,
    session_id TEXT, parent_session_id TEXT, agent_id TEXT,
    call_kind TEXT NOT NULL, call_label TEXT, origin_pid INTEGER,
    provider TEXT NOT NULL, api TEXT, model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_total REAL NOT NULL DEFAULT 0, cost_input REAL, cost_output REAL,
    cost_cache_read REAL, cost_cache_write REAL, cost_source TEXT, token_source TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_usage_ts ON usage_events(ts);
CREATE INDEX ix_usage_model_ts ON usage_events(model_id, ts);
CREATE INDEX ix_usage_session ON usage_events(session_id);
CREATE INDEX ix_usage_kind_ts ON usage_events(call_kind, ts);
```

WAL mode, supporting concurrent appends from subprocesses. SQLite uses the stdlib `sqlite3`, zero external dependencies.

Why not extend session meta: that is the git session's idx.meta JSON, which cannot aggregate across sessions / query by time bucket / index per-model; and a subprocess writing it would also contend for the git lock.

Aggregation API `UsageLedger.query(since, until, group_by=[...], filters={...}, time_bucket="day"|"hour"|None)`, consumed from one source by both the panel (trend = bucket, bar chart = group_by) and the CLI.

The backend is abstracted as an interface (append/query), defaulting to SQLite, leaving a hook for JSONL/remote implementations.

## 8. Subprocess Boundary

A `@agentic_function` runs its body in a subprocess, and the subprocess's internal LLM calls (gui_agent, etc.) have a default_tracker that is an in-process singleton the main process cannot see.

**The subprocess writes the shared SQLite ledger directly; the ledger is the source of truth.**
- The subprocess opens the same usage.db (WAL is multi-process safe), its own recorder appends directly, and `origin_pid` marks the source. No second accounting pass by the main process is needed (avoiding double counting).
- SIGKILL risk: an already-appended event is correct in the DB (flushed to disk by WAL) — those tokens really were spent; a call that did not finish before the kill received no done event, is not accounted for, which matches "never fabricate".

`process_runner.py` uses **spawn rather than fork**: the parent worker has already loaded PyTorch/libomp/Cocoa, and these libraries are in an unsafe state after fork and would SIGSEGV. spawn does not copy contextvars, so the scope is passed explicitly — on the parent side, `run_agentic_in_subprocess` calls `metering.context.snapshot()` to serialize the current UsageContext into a dict, passed as the `usage_ctx_snapshot` parameter to `_child_entry`; on the child side the entry point (after `os.setpgrp()`) calls `apply_snapshot()` to restore it. The ledger's `_connect()` detects a change in `os.getpid()` and reopens the sqlite connection (the old handle is unusable after fork/spawn), so the subprocess gets its own handle onto the shared WAL db.

Snapshot/restore reuses `context.py`'s `snapshot()`/`apply_snapshot()` and process_runner adds two call sites; there is no separate `metering/subprocess.py` module for the boundary. The result pickle does not carry a usage_summary back — the panel queries the subprocess's events from the ledger directly, so returning them adds nothing.

## 9. Extension Hooks (designed, not built)

- per-user: add `user_id` to UsageEvent (defaults to single user, multi-tenancy injected via usage_scope), query(group_by=["user_id"]).
- rate limiting/alerts: UsageRecorder.record() exposes a list of post-record hooks (register_usage_hook), event-driven and non-blocking on the hot path.
- export: add export(format, filters) to the ledger backend interface, or a JSONL mirror backend.
- remote aggregation: swap the backend for a push OTLP/collector implementation, with the event schema unchanged.

## 10. Boundary with UsageTracker

`UsageTracker` (`context/usage.py`) and `UsageLedger` have separate responsibilities and coexist without reading or writing each other.

Tracker is the **compaction budget state machine**. It answers, for `ContextEngine.prepare/compact`, what the real input_tokens were last turn, what the cache hit rate was, and whether to compact — sub-μs hot-path reads, cached per session, persisted to session meta `_usage`. That persistence is the compaction decision's state, not a billing ledger.

Ledger is **billing accounting**: append-only, cross-process, with per-model / per-source / time series.

The two have different consumers, lifecycles, and data shapes, so merging them would couple them rather than simplify. `UsageState` and session meta `_usage` stay as they are because compaction reads them.

Metering reuses without modifying: `models.py:calculate_cost`, `_event_parsing.py:extract_usage`, `dispatcher/persistence.py` message columns, `types.py:Usage/UsageCost`.

## Appendix: Implementation Status

Sections 1–8 and 10 are implemented. `webui/routes/usage.py` queries the ledger and serves `/api/usage/summary` and `/api/usage/trend` (day/hour bucket time series plus a by_kind breakdown, with since/until inputs); the frontend panel draws a trend line, by_source bars, a per-model table, and a cost card.

Two design notes worth keeping, because the code shape is not obvious from the design alone:

- Accounting fires **at the terminal event, before the yield**, guarded by a `recorded` flag. The consumer of the async generator (`agent_loop`) returns directly on the done event, which suspends the generator at its `yield` — anything placed after the loop would never run.
- The former `agent/compaction/` directory (about 1180 lines) was dead code with no import, dynamic reference, or side-effect load, and was deleted; nothing in it needed a usage scope.

Designed but not built: §9 extension hooks, and a CLI `op usage` command.
