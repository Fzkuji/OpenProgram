# Turn Cancellation — Design

> How a running turn is stopped. This document is authoritative for the
> cancellation signal itself: what object carries it, how long it lives, and
> which code checks it. The async Task entity's own lifecycle (queueing,
> worker pool, persistence) is
> [`async-task-lifecycle.md`](async-task-lifecycle.md); a task's cancellation
> uses the mechanism described here.

## 1. One token per turn

**A turn opens exactly one cancellation token. Everything inside the turn
checks that one object, and the token is retired when the turn ends.**

Stopping means tripping the token of the turn that is running *now*. A token
belonging to a turn that already finished refuses to be tripped, so a stop
arriving late cannot reach the next turn. There is no session-level flag to
reset, because there is no state that outlives the turn: the next turn simply
gets a different object.

This is the property that makes the design correct rather than merely tidy. A
sticky per-session boolean has to be cleared by whoever finishes the turn, and
every path that forgets — an early return, a swallowed exception, a stop that
lands between turns — poisons the next message. Scoping the signal to the turn
removes the cleanup obligation instead of adding another place to remember it.

## 2. The token

`CancelToken` in `openprogram/webui/_pause_stop.py`:

```
CancelToken:
  session_id   which session this turn belongs to
  turn_id      the turn, when the caller supplies one
  event        a threading.Event, so blocked worker threads can wait on it
  retired      once True, cancel() does nothing
```

The Event is the interoperability surface. Worker threads block on it, the
dispatcher bridges it into asyncio, and provider streams take it as their abort
signal — all without knowing about the token wrapper.

| Operation | Meaning |
|---|---|
| `begin_turn(session_id, turn_id=None)` | Open a fresh token and register it as the session's current one. Any token still registered belongs to a turn that already ended and is retired here. |
| `end_turn(session_id, token=None)` | Retire the token and deregister it. Passing the token deregisters only that one, so a turn finishing late never unregisters its successor. |
| `current_token(session_id)` | The token of the turn running on this session; `None` between turns. |
| `token.cancel()` | Trip it. Returns `False` when the turn already ended. |
| `token.is_cancelled()` | Whether this turn was stopped. |

## 3. Life of a token

```
begin_turn ──▶ running ──┬──▶ cancel()  ──▶ cancelled ──▶ end_turn ──▶ retired
                         └──────────────────────────────▶ end_turn ──▶ retired
```

A token is created at turn start, is the only cancellation signal for the
duration of the turn, and is retired at turn end whether the turn succeeded,
failed, or was stopped. Retirement is one-way. After it, `cancel()` returns
`False` and the token can never influence anything again.

Two races are closed by construction:

- **A stop racing turn teardown.** It lands on a retired token and dies there,
  rather than setting a flag the next turn would read.
- **A turn finishing after its successor started.** `end_turn` deregisters only
  the token it was given, and `begin_turn` retires whatever it displaced, so
  neither turn can retire the other's token.

## 4. Who checks the token

Every layer inside a turn checks the same object. There is no second signal.

| Layer | Where |
|---|---|
| `@agentic_function` entry and `Runtime.exec` | `_cancel_hook`, registered once at import via `add_pre_invocation_hook`; raises `CancelledError`. |
| LLM calls | `check_cancelled()` immediately before the provider call, converted to `ExecInterrupt("cancelled")`. |
| The agent loop and streaming | The token's Event, bridged into an `asyncio.Event` and passed to `agent_loop` as its abort signal. |
| Tool execution | The same Event, handed to `tool.execute(...)` as its `cancel` argument. |
| Long-running tool bodies | `check_cancelled()` between heavy synchronous stages, so a stop lands without waiting for the next function boundary. |
| Sub-tasks and sub-agents | The task's token; the runner registers its Event via `register_cancel_event`. |

`CancelledError` derives from `BaseException` on purpose, so a tool body's
`except Exception` cannot swallow a stop.

### Resolving which token a frame checks

A frame checks the context-bound token first, and only falls back to the
session registry when no token is bound. This matters when a session has moved
on to a new turn while an older frame is still live: the old frame keeps
checking its own turn's token, so a stop aimed at the new turn does not abort
it, and a stop aimed at its own turn still does.

## 5. The cancel bridge

The dispatcher runs the agent loop in a fresh asyncio loop on a worker thread,
so the thread-side Event must reach the coroutine. A bridge thread waits on the
Event and sets an `asyncio.Event` through `call_soon_threadsafe`.

The bridge also watches a turn-over Event, released when the drain loop
finishes. Without it the bridge parks on `cancel_event.wait()` for the life of
the process — one leaked thread per turn — and would eventually post to a
closed loop.

## 6. Compatibility

The public names keep their meaning, so callers and the WS protocol are
unchanged:

| Name | Now means |
|---|---|
| `mark_cancelled(session_id)` | Stop the turn running on this session. A no-op between turns. |
| `is_cancelled(session_id)` | Whether the current turn is cancelled. `False` once it ends. |
| `clear_cancel(session_id)` | Retire the session's token — the turn is over. |
| `register_cancel_event(session_id, ev)` | Adopt a caller-owned Event as this turn's token. |

Call sites that create their own Event — the chat path and the task runner —
keep doing so; `register_cancel_event` wraps it in a token, which retires the
previous turn's token in the same call.

The two-stage WS `stop` action is unchanged: a graceful stop first, escalating
after a grace period to tripping the token, killing the exec subprocess,
unblocking pending questions, and marking still-running rows `cancelled`.

## 7. Recorded outcome

A user cancel writes `status = cancelled` on the node, never `error` — see the
status vocabulary in
[`../dag/session-dag.md`](../dag/session-dag.md). A cancelled turn is
terminated like any other turn: it keeps whatever output was streamed before
the stop, and it is committed.

## 8. Invariants

1. A turn checks exactly one cancellation token, and every layer inside the
   turn checks that same one.
2. A stop affects only the turn running when it arrives. It can never affect a
   later turn.
3. A token is retired exactly once, at turn end, regardless of outcome.
4. No cancellation state survives a turn, so no cleanup path is obliged to
   reset anything.
5. No thread outlives the turn that started it.

## Appendix: Implementation Status

Implemented. `CancelToken`, `begin_turn`, `end_turn` and `current_token` live
in `openprogram/webui/_pause_stop.py`; the cancel bridge is in
`openprogram/agent/dispatcher/__init__.py`. Tests:
`tests/unit/test_turn_cancellation.py`.

Pause/resume (`pause_execution` / `resume_execution`) is a separate, global,
cooperative mechanism and is not part of the per-turn token model. `/api/stop`
resumes before stopping so a paused turn can be cancelled.

## Related Files

- [`async-task-lifecycle.md`](async-task-lifecycle.md) — the async Task entity
- [`../dag/session-dag.md`](../dag/session-dag.md) — status vocabulary, failure and retry
- [`../../error-handling.md`](../../error-handling.md) — exception discipline
