# Usage Metering Subsystem Design

Status: In progress (2026-06)
Author: design and implementation driven together

## 1. Goals

Ensure **every LLM call** in the framework has its tokens / model / cost recorded with no gaps, carrying source labels, time series, and aggregation by model / source / session. This supports the visualization panel and cost accounting, and leaves extension points for future quotas, rate limiting, budget alerts, and export.

An explicit compromise we will not make: we keep no compatibility code for the old cumulative snapshot-style accounting; old responsibilities that should be split or deleted will be split or deleted.

## 2. Diagnosis of the Current State (before implementation)

Every LLM call ultimately goes through `providers/stream.py`'s `stream_simple()` (streaming) / `complete_simple()` (non-streaming, which runs stream_simple internally). The returned `AssistantMessage` carries `usage` (a `Usage` object: input/output/cache_read/cache_write/total_tokens/cost).

Accounting only covers "the main path that goes through engine", in two layers:
- Message level: `dispatcher/persistence.py` writes tokens into the assistant message's history columns (used for the per-message pill).
- Session cumulative level: `context/usage.py`'s `UsageTracker.record_turn()` writes into the git session meta's `_usage` (only cumulative totals — no time series, no per-model, no per-source, no cost).

Three fundamental problems:

1. **The collection point is not unified.** `stream.py` itself does no accounting; accounting happens in the dispatcher one layer up. Meanwhile `memory/llm_bridge.py` calls `api_provider.stream_simple()` directly and bypasses stream.py entirely. "Went through stream_simple" ≠ "was accounted for".

2. **Storage is a cumulative snapshot, not a stream of events.** The session meta `_usage` cannot be aggregated across sessions, cannot be queried by time bucket, and has no per-model index.

3. **Coupled responsibilities.** `UsageTracker` simultaneously does accounting, compaction threshold estimation (`estimated_input`/`record_compaction`), and hot-path budget caching — three concerns with entirely different lifecycles and consumers.

**List of unaccounted paths** (calling complete_simple/stream_simple directly, bypassing engine):
`context/summarize.py`, `agent/compaction/compaction.py`, `agent/compaction/branch_summarization.py`, `functions/tools/mixture_of_agents`, `memory/llm_bridge.py`, and `@agentic_function` subprocesses (`process_runner.py`, where the in-process singleton tracker is invisible to the main process and the returned result contains no usage).

One favorable fact: `providers/models.py:calculate_cost(model, usage)` can already compute cost from `Model.cost`. The metering layer only needs to ensure it gets called at the collection point; no new pricing logic is required.

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
- `subprocess.py` — subprocess accounting bridge
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

## 5. Propagating the Source Label: contextvar primary, explicit metadata override as fallback

The underlying `stream_simple` does not know who called it. Three ways to pass it:

| Approach | Pro | Con |
|---|---|---|
| Explicit parameter `options.call_kind` | Explicit | Every caller has to change, threads through many layers of signatures, violates "adding a caller doesn't touch the underlying layer" |
| `SimpleStreamOptions.metadata` | Field already exists | Easy to miss in deep calls; also doesn't reach when memory bypasses stream.py |
| **contextvar** | One line `with usage_scope(...)`, async Tasks inherit automatically | Not propagated automatically across processes/threads (needs an explicit snapshot) |

We adopt contextvar as primary + explicit metadata override. `metering/context.py`:
`usage_scope(call_kind, call_label, parent_session_id, agent_id)` context manager, set/reset the contextvar, supports nested merge. `current_usage_context()` reads it. `snapshot()`/`apply_snapshot()` serialize across processes.

Boundary notes: asyncio Tasks created by default use `copy_context()`, so stream_simple downstream of create_task can read the correct scope. Thread boundaries (run_in_executor/raw Thread) do not inherit → call apply_snapshot at the entry point. The process fork boundary copies the contextvar's current value (favorable for process_runner fork), but spawn does not → snapshot/apply_snapshot as the reliable path.

recorder merge priority: `metadata.usage` > contextvar > default `unknown`.

## 6. Collection Point: wrap stream.py + pull memory back in

Wrap an accounting decorator around `stream.py`'s `stream()`/`stream_simple()`, and pull `memory/llm_bridge.py` back into stream.py.

Rationale:
1. stream.py is the semantic boundary of "one logical LLM call", already does api_key resolution / provider lookup, and can obtain the model (with cost) + the final AssistantMessage.usage.
2. stream() is a generator, so the wrapping approach = when consuming done/error, extract final_message.usage → `calculate_cost` → read the contextvar → assemble a UsageEvent → recorder. Streaming is not blocked; accounting fires exactly once on the terminal event.
3. Not the api_registry layer: ApiProvider is a Protocol, each implementation has its own stream_simple, so collecting there means either changing the Protocol (invasive to every provider) or wrapping the registry (scattered wrap points). stream.py is the single-function collection point.
4. memory must be pulled back: currently `llm_bridge.py` connects directly to api_provider = unaccounted. Change it to call `providers.stream_simple` + `usage_scope("memory")`, which incidentally fixes the header inconsistency that a comment in stream.py worried about.

Invariant: accounting failures must be best-effort (swallowed with try/except), and must never interrupt the LLM response.

The dispatcher's existing `persist_assistant_message` (the messages columns) is **left untouched** — that is the data for the per-message pill. The ledger is a separate, second authoritative account: the message columns = "how much did this message cost", the ledger = "an aggregatable global stream".

## 7. Storage: a standalone global SQLite ledger

Drop stuffing a cumulative dict into session meta. Create a new `~/.openprogram/usage.db` with a single append-only table:

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

`@agentic_function` subprocesses: fork runs the tool body, and the subprocess's internal LLM calls (gui_agent, etc.) have a default_tracker that is an in-process singleton the main process cannot see.

Approach: **the subprocess writes the shared SQLite ledger directly (the ledger is the source of truth)**.
- The subprocess opens the same usage.db (WAL is multi-process safe), its own recorder appends directly, and `origin_pid` marks the source. No second accounting pass by the main process is needed (avoiding double counting).
- SIGKILL risk: an already-appended event is correct in the DB (flushed to disk by WAL) — those tokens really were spent; a call that did not finish before the kill received no done event, is not accounted for, which matches "never fabricate".

**Implementation as landed (differences from the first-draft approach)**: `process_runner.py` uses **spawn rather than fork** (the parent worker has already loaded PyTorch/libomp/Cocoa, and these libraries are in an unsafe state after fork and would SIGSEGV). spawn does not copy contextvars, so we rely on explicit parameter passing: on the parent side, `run_agentic_in_subprocess` calls `metering.context.snapshot()` to serialize the current UsageContext into a dict, passed as a new `usage_ctx_snapshot` parameter to `_child_entry`; on the child side the entry point (after `os.setpgrp()`) calls `apply_snapshot()` to restore it. The ledger's `_connect()` detects a change in `os.getpid()` and automatically reopens the sqlite connection (the old handle is unusable after fork/spawn), so the subprocess automatically gets its own handle to write the shared WAL db. **There is no separate `metering/subprocess.py`** — snapshot/restore directly reuses `context.py`'s `snapshot()`/`apply_snapshot()`, and process_runner only adds two call sites, which is more restrained than a standalone module. The result pickle does not yet send usage_summary back (the panel can already query the events written by the subprocess from the ledger in real time, so immediate display adds no value).

## 9. Extension Hooks (not implemented)

- per-user: add `user_id` to UsageEvent (defaults to single user, multi-tenancy injected via usage_scope), query(group_by=["user_id"]).
- rate limiting/alerts: UsageRecorder.record() exposes a list of post-record hooks (register_usage_hook), event-driven and non-blocking on the hot path.
- export: add export(format, filters) to the ledger backend interface, or a JSONL mirror backend.
- remote aggregation: swap the backend for a push OTLP/collector implementation, with the event schema unchanged.

## 10. Deletion / Refactor List

### 10.1 Actually Executed (landed in Phase 4)

- **Deleted the entire `agent/compaction/` directory** (`__init__.py`/`compaction.py`/`branch_summarization.py`/`utils.py`, about 1180 lines): confirmed no external import, no dynamic reference, no side-effect loading — it is genuinely dead code.
- **`webui/routes/usage.py` fully rewritten**: changed from scanning `session_db` + reading session meta `_usage` to querying `UsageLedger`; added `/api/usage/trend` (day/hour bucket time series) and a by_kind breakdown, with inputs supporting since/until.

### 10.2 Revised Plan: UsageTracker is kept, not split

The first draft planned to split `UsageTracker` (context/usage.py) into 3 responsibilities, delete `record_turn`/`_persist`/`_load_from_db`, and extract `context/budget_state.py`. **This was vetoed during implementation**, for these reasons:

- `UsageTracker` and `UsageLedger` **already have separate responsibilities** and do not conflict. Tracker is the **compaction budget state machine** — it answers, for `ContextEngine.prepare/compact`, "what was the real input_tokens last turn, what was the cache hit rate, should we compact" — with sub-μs hot-path reads, caching per session, writing to session meta `_usage` (persistence of the compaction decision, not a billing ledger). Ledger is **billing accounting** — append-only, cross-process, with per-model/per-source/time series. The two have entirely different consumers, lifecycles, and data shapes; forcing a merge would instead couple them.
- Deleting `record_turn` would touch `engine.after_turn`, a core hot path (the context compaction chain) unrelated to this task, violating the surgical-change principle, introducing regression risk, with zero benefit (session meta `_usage` is tiny, harmless to keep).
- Extracting `budget_state.py` is "splitting for the sake of splitting" — Tracker is currently a clean single-responsibility implementation (despite the name Tracker, it is in essence budget state), and there is no real pain point to address.

Conclusion: **Tracker is kept as is**, and `UsageState`/session meta `_usage` are kept as is (compaction uses them). Billing accounting is handled entirely by the new Ledger, which coexists with Tracker, neither reading nor writing the other.

Untouched (inputs to metering, reused): `models.py:calculate_cost`, `_event_parsing.py:extract_usage`, `dispatcher/persistence.py` message columns, `types.py:Usage/UsageCost`.

## 11. Phased Implementation (all complete)

- **Phase 0 ✅**: metering foundation (event/context/ledger/recorder + unit tests), no behavior change.
- **Phase 1 ✅**: stream.py collection point; dispatcher chat wrapped in usage_scope. Key fix: the consumer of the async generator (agent_loop) does a `return` directly upon receiving the done event, suspending the generator at the `yield` — so accounting after the loop never executes. Changed to **account at the terminal event, before the yield** (a `recorded` flag prevents double counting).
- **Phase 2 ✅**: summarize / mixture_of_agents(proposer+aggregator) wrapped in usage_scope; memory/llm_bridge pulled back from directly calling `api_provider.stream_simple` to going through `providers.stream_simple` (so the collection point takes effect) + wrapped in `usage_scope(call_kind="memory")`. (The compaction/branch path disappeared along with the deletion in §10.1, so no wrapping needed.)
- **Phase 3 ✅**: UsageContext passthrough for spawn subprocesses (snapshot/apply_snapshot via parameter, see §8 implementation as landed); ledger pid re-check auto-reconnect. Did not implement sending result usage_summary back (the panel can already query it in real time).
- **Phase 4 ✅**: deleted the `agent/compaction/` dead code; switched the usage route to the ledger. **Did not split UsageTracker** (see §10.2 revised plan).
- **Phase 5 ✅**: `/api/usage/summary`+`/api/usage/trend` query the ledger; the frontend panel has a trend line (ResizeObserver real-pixel drawing, avoiding viewBox stretch distortion) + by_source horizontal bars + a per-model table + a cost card; the color scheme uses `--accent-blue` (the brand warm orange). Did not implement the CLI `op usage` (left as a hook, to be added as needed).
