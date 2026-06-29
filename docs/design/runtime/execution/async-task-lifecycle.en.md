# Async Task Lifecycle

> Change sub-agent invocation from synchronous blocking to "explicit task entity + background worker + queryable and cancellable." Aligns with Claude Code's TaskCreate / TaskList / TaskGet / TaskUpdate / TaskStop.
>
> Current baseline: `run_agent_turn` (`openprogram/agent/sub_agent_run.py`) blocks synchronously → calls `process_user_turn` → returns `AgentTurnResult`. The `/task` tool, the `/spawn` WS action, and `_merge.process_merge_turn` all reuse this synchronous path directly.
> Collaboration machinery the WebUI already has: one worker thread per session (`_execute_in_context`), one `_cancel_events` dict (`_pause_stop.py`), and one `_running_tasks` dict for the UI spinner. These are the chassis; the new Task abstraction layers on top of them.

---

## Part 1. Dimensions to consider for the Async Task lifecycle

Anything that needs a full spawn / list / get / cancel lifecycle must nail down the 15 points below. Each scenario later fills out this checklist item by item.

### D1. What the Task entity stores

A `Task` must carry at least:

- `task_id`: a stable id returned immediately on spawn (independent of user_msg_id / assistant_msg_id)
- `subject`: a one-line summary (for list / panel display)
- `description`: the full prompt (reused as `prompt` when invoking the agent)
- `agent_id`: which profile to run
- `status`: the status enum from D2
- `created_at / queued_at / started_at / completed_at`: timestamps
- `parent_session_id`: which session the task runs on (a task is always bound to one session)
- `parent_task_id`: the task that spawned this task (None for a top-level user-spawn)
- `parent_msg_id`: the user / assistant msg id that triggered the spawn (used to hang the attach card back onto it)
- `context_mode`: `inherit` / `clean`
- `head_id`: the assistant msg id where the sub-agent lands after the task completes (None while running)
- `result_text`: the sub-agent's final reply (None while running)
- `error`: the error string on failure
- `cancel_requested_at`: the time the cancel signal was written
- `attempt`: the retry count (fixed at 0 in the first version)

Don't stuff runtime objects like `cancel_event` / `future` into the entity; the entity only describes "what," while "how to cancel / wait" is resolved by an internal map in the runner (see D5).

### D2. State machine

```
pending → queued → running → completed
                          ↘ cancelled
                          ↘ errored
```

- `pending`: the spawn API received the request, the entity was written to the persistence layer, but it hasn't been scheduled onto the worker pool yet.
- `queued`: handed to the ThreadPoolExecutor but not yet picked up (all workers busy).
- `running`: a worker picked it up and started `process_user_turn`.
- `completed`: the sub-agent returned normally, and `head_id` and `result_text` are written.
- `cancelled`: the cancel event was consumed and the worker exited (partial output may have landed).
- `errored`: the worker threw an exception, or a task was unfinished when recovering from a process crash.

Every state transition updates the corresponding timestamp and is irreversible. Jumping straight from `pending → cancelled`, skipping queued / running, is also allowed (the user stops before the worker picks it up).

### D3. Worker pool model

Use `concurrent.futures.ThreadPoolExecutor`, isomorphic to the existing daemon-thread model of `_execute_in_context`, and without async-coloring the whole codebase. Rationale:

- `process_user_turn` already opens its own `asyncio.new_event_loop()` internally to run the agent loop, so wrapping it in an outer thread causes no double-event-loop conflict.
- BashTool and file IO are blocking calls; the thread model costs nothing for them.
- The concurrency cap is configured via the `OPENPROGRAM_TASK_WORKERS` environment variable, default 4. Tasks over the cap stay `queued`, FIFO-fair.
- Backpressure: no per-session limit (tasks are siblings, and the serial spinners shown in the UI don't affect this); a global pool cap is enough. Add priority later if needed.

### D4. Persistence

Reuse the existing session-git meta: alongside `meta.json` in the session repo, add a `tasks.json` with structure `{task_id: TaskRow}`. Each task state transition calls `commit_turn("task: ...")` once, landing it in git history.

Rationale:

- A task is always bound to a single session (D6), so storing it under the session directory partitions it naturally.
- Sessions already follow git-as-truth; putting task state into git history actually helps debug "why is the task stuck."
- No need for a new SQLite table or a new DB file.

Non-recoverable policy: after a process crash, all tasks with status=`running` / `queued` / `pending` are marked `errored` at startup (error="worker died before completion"). The reasoning: the LLM call was already issued and can't be retrieved, so making the user re-spawn is the cleanest semantics.

### D5. Cancel signal propagation

Each running task has a `threading.Event` inside the runner (not stored in the entity). When the Cancel API fires:

1. Write `cancel_requested_at` to the entity and transition status to `cancelled` (if still queued / pending), or keep it `running` and wait for the worker to exit naturally.
2. `cancel_event.set()` — via the contract already defined by `_pause_stop.register_cancel_event`, this propagates to `process_user_turn(cancel_event=...)`.
3. `process_user_turn` already bridges cancel_event into an asyncio.Event (the `agent_loop` call), and the LLM provider stream checks for the cancel on the next chunk and then breaks.
4. BashTool / other subprocesses: reuse `_pause_stop.kill_active_runtime` (already exists). Cancellation at the tool layer is cooperative: each `@agentic_function`'s pre-invocation hook checks `is_cancelled` (already exists), and the next tool-call entry raises `CancelledError`.
5. Fallback timeout: if the worker still hasn't exited 30 seconds after the cancel, the runner marks the entity `cancelled` (error="cancel timed out, worker may be stuck") and detaches the worker thread (no hard kill; let GC handle it).

A tool's own atomic operation (e.g. a `Write` halfway through) is not interrupted; it waits for the current atomic operation to finish before exiting.

### D6. The relationship between Task and session

A task always runs on **one** parent session. Cross-session is conceptually equivalent to merge / attach (already exists) and is out of scope for tasks.

`task.parent_session_id` is exactly the one the sub-agent uses in `process_user_turn(session_id=...)` — consistent with how `run_agent_turn` behaves today. The sub-agent's output lands as a branch of that session (or a new root, depending on `context_mode`), so the session repo holds both the task entity and the task's product, which is self-consistent.

### D7. The relationship between Task and sub-agent

`/task` no longer blocks synchronously. All spawns go through the task entity. The semantic shift:

- The agent-facing tool `task(prompt, ...)` defaults to **async** — it returns `task_id` immediately without blocking the main conversation. Once the LLM has the id, it can choose to do other things, or immediately call `await_task(task_id)` to replicate the old synchronous semantics (D15).
- Compatibility flag: `/task --sync` (or a `wait=True` parameter) wraps a `spawn → await` at the tool layer, returning the result synchronously, transparently to the LLM.
- The old `_task_impl` implementation still keeps the `run_agent_turn` call, but routes through the runner entry point.

`/spawn` (the user typing `/spawn label: prompt` in chat) also goes through the same spawn API; the only difference is the caller is a WS handler rather than the LLM.

### D8. The relationship between Task and ContextCommit

The attach pointer (the `function="attach"` node) has always been written by `_run_spawn` / `_task_impl` after the sub-agent finishes. After the rework:

- On spawn, immediately write a **placeholder attach card** (`function="attach"`, `extra.attach.task_id = <task_id>`, `extra.attach.status = "running"`), content="(running)". Leave `source_commit_id` empty.
- When the task completes, the runner updates the same attach card node: fill in `head_id` / `source_commit_id`, replace content with `final_text`, and change `status` to `completed` / `cancelled` / `errored`.
- When the generator sees an attach node with `status="running"`, it skips expansion (does not enter commit items) and only shows the card placeholder in the UI. When it sees `status="completed"`, it follows the existing attach-expansion path (see scenario B in `context.md`).

This way, when the LLM triggers a new turn before the task finishes, it won't see half-baked attach content, but the user can still see the spinner.

### D9. WS API

Add four ws actions (following the existing naming in `ws_actions/`):

- `spawn_task` — `{action, session_id, prompt, description, agent_id?, context?, wait?}` → immediately returns `{task_id, status, parent_msg_id}`.
- `list_tasks` — `{action, session_id?, status_filter?, limit?}` → `{tasks: [...]}`. No session_id means list globally.
- `get_task` — `{action, task_id}` → single entity + current head_id / partial result (if running).
- `cancel_task` — `{action, task_id}` → `{task_id, status}` (returns the current status synchronously; does not wait for the worker to exit).

Broadcast events:

- `task_created`, `task_status` (queued / running / completed / cancelled / errored), `task_progress` (optional; add token progress in the future).
- Reuse the existing `running_task` broadcast but add a `task_id` field (UI compatibility).

### D10. Agent-facing tools

The agent uses a trio:

- `spawn_task(prompt, description, agent_id?, context?, wait=False)` → returns `task_id` (wait=False) or the final result (wait=True). Wraps `runner.submit(...)`.
- `await_task(task_id, timeout=None)` → blocks the calling thread until completed / cancelled / timeout, returning `{status, result_text, head_id, error}`. The LLM calls this to wrap up in concurrent scenarios.
- `cancel_task(task_id, reason?)` → `{ok, status}`.

The variant `await_tasks([id1, id2, ...], mode="all"|"any", timeout)` is used for plan mode's wait_all (D14). The trio plus wait_all makes four tools, but plan-mode does not expose wait_all to ordinary agents — an ordinary agent can only await a single id (to avoid misuse).

### D11. UI representation

- Add a **Tasks** tab to the right-side panel (right next to the existing Branches / Context Commits panel). It lists all task entities for the current session: spinner + subject + status + creation time.
- Open a task: jump to its corresponding attach card (the placeholder already exists in chat) → check out its head_id branch (if completed).
- Each attach card carries its own status badge: `running` / `done` / `cancelled` / `error`. Once complete, behavior is the same as today.
- The global sidebar shows a total count badge (how many running): reuse the existing `running_task` mechanism.

### D12. Error recovery

- Worker throws an exception: the runner catches it → status `errored` → fill the `error` field with `f"{type}: {msg}"` → the attach card status updates in sync → broadcast a task_status event.
- Main process crash: see D4. The startup hook scans `tasks.json` and marks all non-terminal states as `errored`.
- Pool shutdown: wait 5 seconds when the process closes; tasks that time out are marked `errored` ("worker pool shutdown").
- Re-spawning the same task_id is not allowed (the spawn API is idempotent on task_id, but by default mints a new id).

### D13. Test boundaries

Unit tests do not run a real LLM. The seams needed:

- `runner.submit(req, *, sync_fn=...)` — replace `process_user_turn` with a fake synchronous function (which receives cancel_event and returns a fake `TurnResult`).
- The entity store uses an in-memory `MockTaskStore` (a dict rather than git, but the same interface).
- State machine test matrix: one case per legal transition, one reject case per illegal transition.

Integration tests cover: the spawn → await → completed path, spawn → cancel mid-flight, spawn → worker raise → errored, multi-task FIFO queuing, and crash recovery marking running as errored.

### D14. Plan mode integration

The plan agent produces a plan (a set of sub-task specs); when exit_plan_mode completes:

- The plan tool / executor agent receives the spec list (each containing `description` + `prompt` + an optional `agent_id`).
- Call `spawn_task` in sequence to get N task_ids.
- Call `await_tasks(ids, mode="all")` to wait for all to complete.
- Once the results arrive, the executor synthesizes them (writing a user-visible summary, or automatically triggering `merge`).

The concurrency cap is the pool size from D3 — a plan listing 10 tasks with a pool of 4 will leave 6 stuck in `queued`, shown queued in the UI.

### D15. Backward compatibility

`/task` remains the user-input entry point in chat with unchanged behavior (the user sees an attach card + the full result), but it now routes through the task entity underneath.

The LLM-facing `task(prompt, ...)` tool provides two semantics:

- `wait=True` (default, backward compatible): internally spawn + await + return result_text as the return value to the LLM. From the LLM's perspective, identical to today.
- `wait=False`: returns `task_id`, and the LLM decides when to await on its own. Used by new code / plan mode.

The toggle is hidden in the tool signature, so old prompts run unchanged. The tool-capability description broadcast to the LLM explains both modes + recommended usage.

---

## Part 2. Walking each scenario through the dimensions

### Scenario A: a single sync `/task` (current baseline)

The most common case: the LLM calls `task(prompt="probe X")`, expecting to block and get the result. Behavior is unchanged after the rework, but it routes through the task entity underneath.

| Dimension | Design |
|---|---|
| **D1 entity** | Create the entity on spawn (with prompt / agent_id / context_mode = inherit), `parent_task_id=None`, `wait=True` |
| **D2 state machine** | Still goes through the full pending → queued → running → completed |
| **D3 worker pool** | Submit to the pool; if the pool is full, wait queued (under sync semantics the LLM perceives a little latency but the result is unchanged) |
| **D4 persistence** | Full flow: write tasks.json + git commit on every state transition |
| **D5 cancel** | User stops the session → the cancel event propagates to the task → the sub-agent loop breaks; final status=`cancelled`, the tool returns partial output + a `[cancelled]` marker |
| **D6 session binding** | parent_session = the caller's session (unchanged) |
| **D7 sub-agent** | The tool wrapper spawns + awaits internally: zero change to the LLM call site |
| **D8 ContextCommit** | The placeholder attach card exists briefly (milliseconds to seconds, since it waits synchronously for the result) and is updated immediately on completion; the UI barely sees the running state |
| **D9 WS API** | The tool call goes through the in-process API (no need to go via WS); the UI can still see it via list_tasks |
| **D10 agent tool** | `task(...)` defaults to wait=True, covering 99% of existing code paths |
| **D11 UI** | The Tasks panel flashes once; the attach card appears directly in the completed state |
| **D12 error** | Worker throws → status=`errored` → the tool gets a `[task error] ...` string (preserving the existing error format) |
| **D13 test** | unit: mock the runner, verify spawn + await are called twice in order; integration: actually run a trivial agent |
| **D14 plan mode** | Not applicable (this is a single task) |
| **D15 compatibility** | Tool signature unchanged, zero changes to old code |

---

### Scenario B: a single async task (the agent actively chooses async)

The LLM decides to do a long job, first spawns to get an id, then awaits or cancels later.

| Dimension | Design |
|---|---|
| **D1 entity** | Create on spawn, wait=False; the entity is written to disk immediately |
| **D2 state machine** | When spawn returns, it's usually already `queued` or `running`, transparent to the LLM |
| **D3 worker pool** | Submit does not block the caller thread; the caller LLM continues to the next tool call |
| **D4 persistence** | Same as A |
| **D5 cancel** | The LLM calls `cancel_task(id)`, or the user cancels in the UI; both paths are the same (both go into runner.cancel) |
| **D6 session binding** | Same as A |
| **D7 sub-agent** | spawn / await are decoupled: between the two tool calls the LLM can read files, search code, etc. |
| **D8 ContextCommit** | The placeholder attach card is written in the spawn turn, status=running; on subsequent turns the LLM still sees this block as a placeholder in the ContextCommit (the generator sees status=running and does not expand) |
| **D9 WS API** | spawn_task → returns task_id; the UI immediately sees a new row in the Tasks panel |
| **D10 agent tool** | `spawn_task` returns `task_id` to the LLM; a subsequent `await_task(task_id)` retrieves the result |
| **D11 UI** | A running row in the Tasks panel + the sidebar total count + an attach card with a status badge |
| **D12 error** | Same as A, but the LLM perceives the error only at await time (it can also discover it earlier via get_task before awaiting) |
| **D13 test** | unit: after spawn returns task_id, status = queued/running; after await, the state transition is correct |
| **D14 plan mode** | The basic building block of plan mode |
| **D15 compatibility** | A new tool; old prompts won't trigger it |

---

### Scenario C: N concurrent async tasks (plan mode)

The plan agent lists 5 research tasks, spawns 5 tasks, and calls `await_tasks(ids, mode="all")` to wait for them all.

| Dimension | Design |
|---|---|
| **D1 entity** | 5 entities, with `parent_task_id` all pointing to the plan agent's current turn (`parent_msg_id`), making it easy for list_tasks to group by plan |
| **D2 state machine** | With pool size=4, 4 go → running and 1 → queued; the first to finish transitions to completed, and the queued one starts running |
| **D3 worker pool** | The key scenario. FIFO-fair; when the pool is full, spawn returns task_id immediately with status=`pending`/`queued`, and await_tasks waits automatically |
| **D4 persistence** | Each task gets its own row in tasks.json; commit on every state transition. Expect roughly ~10-20 git commits for one plan |
| **D5 cancel** | `cancel_task(id)` cancels one; to cancel the whole plan, the plan agent iterates and cancels all children itself (could also add a `cancel_tasks(parent_msg_id=...)` batch API as a future addition) |
| **D6 session binding** | All 5 tasks run on the same parent_session; after landing they are 5 parallel branches (branch label = task description) |
| **D7 sub-agent** | 5 concurrent sub-agents run at the same time, isolated by the thread pool; the ContextVar (`current_session_id`) is thread-local and they don't interfere with each other |
| **D8 ContextCommit** | 5 placeholder attach cards line up hanging off the plan agent's fork point; completion order doesn't matter, and the UI updates each one independently. On subsequent LLM turns, these 5 blocks in the ContextCommit are attach expansions, handled per scenario C in `context.md` |
| **D9 WS API** | spawn 5 times + 1 await_tasks (wrap a server-side wait-aggregation to avoid the LLM polling repeatedly) |
| **D10 agent tool** | The plan agent uses `spawn_task` ×5 + `await_tasks(mode="all")` ×1 |
| **D11 UI** | 5 rows in the Tasks panel; one of them in queued state has a clock icon. The completed ones flip to completed one by one |
| **D12 error** | Partial failure: await_tasks returns the list once all terminal states are collected, each carrying its own status / error; the plan agent decides partial vs. retry itself |
| **D13 test** | The key coverage is pool backpressure: spawn 6 tasks with pool=2, assert tasks 3-6 stay queued until the first two complete |
| **D14 plan mode** | This is the primary scenario for plan mode |
| **D15 compatibility** | A new API, no impact on old code |

---

### Scenario D: a long-running task + cancel

The agent spawns a 30-minute deep research task; 10 minutes in, the user clicks Stop in the UI or the agent decides to abort.

| Dimension | Design |
|---|---|
| **D1 entity** | Same as B; on cancel, fill `cancel_requested_at` |
| **D2 state machine** | running → cancelled (or forced cancelled if the worker doesn't exit within the timeout) |
| **D3 worker pool** | The thread stays occupied until the worker actually exits; the pool slot is released after the worker function returns |
| **D4 persistence** | The cancel request is committed immediately; commit again after the worker exits (final state) |
| **D5 cancel** | This is the design core. cancel_event.set() → (a) the asyncio.Event inside `process_user_turn` fires → agent_loop breaks on the next stream chunk → the LLM call aborts; (b) the `is_cancelled(session)` hook makes the next `@agentic_function` raise CancelledError; (c) BashTool kills subprocesses via `kill_active_runtime`; (d) the 30-second fallback timeout force-transitions the status |
| **D6 session binding** | Unchanged |
| **D7 sub-agent** | After the sub-agent loop gets the cancel, it follows the dispatcher's existing cancelled branch: the placeholder is already inserted, the error folds into the same row → status=cancelled, and partial output lands on disk |
| **D8 ContextCommit** | The attach card status goes from running → cancelled; content writes partial output + a `[cancelled at T]` marker; the generator can also selectively expand cancelled (first version: don't expand cancelled, just show the marker) |
| **D9 WS API** | cancel_task returns immediately (doesn't wait for the worker), the UI shows a "cancelling..." state; another task_status broadcast goes out when the worker actually exits |
| **D10 agent tool** | The LLM can call `cancel_task(id)`; a subsequent `await_task(id)` returns the cancelled terminal state immediately |
| **D11 UI** | The Stop button already exists; clicking it triggers cancel_task; the spinner turns into a spinner + dim color until the worker exits |
| **D12 error** | cancel timeout: after 30s, force-transition the status but keep the worker thread; log a warn; the UI hints "task may still be running in background" |
| **D13 test** | The key test: a fake sync_fn deliberately ignores cancel_event for 30 seconds, assert the runner force-transitions to cancelled state after 30s |
| **D14 plan mode** | The plan agent may proactively cancel the remaining queued tasks on partial completion (to save budget) |
| **D15 compatibility** | When sync /task defaults to wait=True, the user stopping the session cancels both the sub-agent and the parent turn (existing behavior, unchanged) |

---

## Part 3. The gap between current state and target

| Capability | Current | Target | Gap |
|---|---|---|---|
| Task entity | None (only in-mem `_running_tasks` for the spinner) | Full entity + persistence | Large |
| State machine | Implicit (function return = done) | Explicit 5 states + transition rules | Large |
| spawn returns immediately | Blocking call | Returns task_id immediately | Large |
| Query interface | None | get_task / list_tasks | Large |
| Cancel | Whole-session level (`_cancel_events`), no task granularity | Single-task cancel | Medium (infrastructure already in place) |
| Worker pool | One daemon thread per session (`_execute_in_context`) | Unified ThreadPoolExecutor | Medium |
| Persistence | `_running_tasks` in-memory dict (lost on refresh) | session repo tasks.json + git | Medium |
| Plan mode concurrency | None (`/task` is synchronous and serial) | spawn N + await_all | Large |
| attach card placeholder | Written only on completion | Written on spawn with running status | Medium |
| Agent tool | Only `task` (synchronous) | spawn / await / cancel trio | Large |
| UI Tasks panel | None (only the sidebar spinner) | A dedicated panel | Medium (frontend) |
| Cross-process task | None | Not for now (Part 6) | — |
| Retry / priority | None | Not for now (Part 6) | — |

---

## Part 4. Change list

In dependency order:

| Step | File | Main change |
|---|---|---|
| 1 | `openprogram/agent/task/types.py` (new) | `TaskStatus` enum + `Task` dataclass (D1) + transition-rules helper |
| 2 | `openprogram/agent/task/store.py` (new) | `TaskStore` interface; persist `tasks.json` in the session repo (D4); also implement `MockTaskStore` for tests |
| 3 | `openprogram/agent/task/runner.py` (new) | `TaskRunner` singleton: `submit / cancel / get / list`; holds the `ThreadPoolExecutor` (D3) + `_cancel_events` (D5); startup hook marks orphan tasks errored (D12) |
| 4 | `openprogram/agent/sub_agent_run.py` | Rework: extract `_run_one(task: Task, *, cancel_event)` wrapping `process_user_turn`; add the async entry `submit_agent_task(...)`; keep `run_agent_turn(...)` but route it internally through `runner.submit(...).result()` |
| 5 | `openprogram/agent/task/agent_tools.py` (new) | `@function` implementations of `spawn_task / await_task / cancel_task / await_tasks`, bound to the toolset (D10) |
| 6 | `openprogram/webui/ws_actions/task.py` (new) | 4 handlers corresponding to D9; register in `ws_actions/__init__.py` |
| 7 | `openprogram/webui/_execute/__init__.py::_run_spawn` | Switch to `submit_agent_task`; write the placeholder attach card with task_id + status=running |
| 8 | `openprogram/context/commit/generator.py` | When handling attach nodes, check `extra.attach.status`: running / cancelled / errored do not expand, only placeholder (D8) |
| 9 | `openprogram/functions/tools/task/task.py` | `_task_impl` internally switches to `submit_agent_task` + defaults to wait=True; returns task_id when wait=False |
| 10 | `web/components/right-sidebar/tasks-panel.tsx` (new) | UI representation (D11); subscribes to the `task_status` ws event |
| 11 | `web/components/chat/messages/attach-card.tsx` | Render the status badge (running / done / cancelled / error) |
| 12 | `openprogram/agent/dispatcher.py::process_user_turn` | On startup, check `OPENPROGRAM_TASK_WORKERS` and initialize the runner singleton (idempotent) |
| 13 | Tests | unit: state machine, runner submit + cancel + crash recovery; integration: spawn → await, N concurrent, cancel mid-flight (D13) |
| 14 | `docs/design/runtime/async-task-lifecycle.md` | This document |

---

## Part 5. Key invariants (verify during implementation)

1. **Terminal state is unique**: every task entity must end in exactly one of completed / cancelled / errored, with no permanent running (pool shutdown / crash recovery must transition it to errored).
2. **State is monotonic**: each edge of the state machine is traversed only once. Once completed, it never goes → cancelled, and running → pending is not allowed.
3. **Cancel is reachable**: after the cancel API returns, the entity must reach a cancelled / errored terminal state within 30 seconds (the forced-timeout fallback).
4. **Placeholder consistency**: the attach card written on spawn stays in sync with the entity's state; every entity state change must update the card in sync.
5. **Persistence is idempotent**: after a crash reload, the state of unfinished tasks is determined to be errored; the same task_id is never revived.
6. **Session binding is immutable**: after a task is created, `parent_session_id` doesn't change; the same task doesn't run on two sessions.
7. **Concurrency safety**: the runner's `_tasks` + `_cancel_events` maps are held under a lock throughout; state reads and writes don't race.
8. **Test controllability**: the runner must accept an injectable `sync_fn` / `store`, so the full state machine can be run without depending on a real LLM.

---

## Part 6. Out of scope for this design

- **Cross-process tasks**: currently all workers are in the main process. Distributed / multi-host is left for later and requires a message broker.
- **Task priority / SLA**: FIFO is enough, with no high-priority preemption. Add a priority queue later as needed.
- **Resume / continuation**: cancelled / errored tasks can't "pick up where they left off." A user retry equals spawning a new task.
- **Task retry policy**: the runner does not auto-retry; the upper-layer agent / plan decides for itself.
- **Cross-session tasks**: a task binds to only one session. Use attach / merge for cross-session.
- **DAG-shaped task dependencies**: the current `await_tasks(mode="all"|"any")` is already enough for plan mode; an explicit DAG / pipeline is left for later.
- **Streaming subscription of task output**: the first version only retrieves final_text on task completion. Subscribing to the stream mid-flight (so the parent agent sees the sub-agent thinking out loud) is left for later.
- **Resource quotas**: per-user concurrent task count / token caps are out of scope for this design and require a multi-tenant model first.
