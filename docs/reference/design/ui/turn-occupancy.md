# Turn occupancy — stop, queue, and the session slot

A session admits **one live turn**. Typing while it runs is a queue, not
a second turn. Stop is an interrupt: occupancy is released on cancel
*intent*, not when the old turn thread dies.

Related: [`interaction-feedback.md`](interaction-feedback.md) (0ms UI),
[`send-queue-reliability.html`](send-queue-reliability.html) (queue
mechanics). Code: `use-chat-submit.ts` (`stopSession`),
`server.py` (`_finish_owned_run` / `_try_reserve_run`),
`execution/control.py` (`RuntimeControlService.request_cancel`),
`run_control.py` (`CANCEL_GRACE_S`),
`providers/utils/cancelable_stream.py`.

## Invariants (from Codex CLI and Claude Code)

The reference implementations agree on five rules; OpenProgram follows
them. Codex CLI: cancellation authority lives on the core session's
running turn; UI running-state derives only from `TurnStarted` /
`TurnCompleted` / `TurnAborted`; there is no protocol-level
`cancelling` state. Claude Code: ESC flips a synchronous state machine
(`QueryGuard`) to idle in the same frame, commits partial streamed
text as a normal assistant message, then aborts; an interrupted turn
has no intermediate status.

1. **One occupancy source of truth per side.** Client: `runningTasks`
   in the session store, mirrored to the composer. Server:
   `_running_tasks` plus the canonical execution driver. Message `status` renders bubbles;
   it never gates the composer, and the composer state never gates
   bubble rendering.
2. **Terminal states are irreversible.** Once a message is `cancelled`
   / `completed` / `failed` / `error` / `interrupted` / `done`, no
   later frame (`execution.updated` with `cancelling`, late
   `running_task`, late `tree_update`) may move it back to a running
   look. `cancelling` is a server-internal grace state; the client
   treats it as already-cancelled and never stores it on the running
   task or renders it as "thinking".
3. **Placeholders never overwrite identity.** An empty
   `{ msg_id: "" }` occupancy reservation may only be written into an
   empty slot. A slot holding `msg_id` / `execution_id` is what makes
   stop able to send `execution.cancel`.
4. **Only the turn's own terminal frame releases occupancy.** A
   nested `display:"runtime"` result (inline @agentic_function, spawn
   attach) finalizes its card, not the turn. Late `running_task`
   frames for a turn the user already cancelled must not revive the
   slot.
5. **Stop never fails silently.** If the server cannot resolve an
   execution to cancel it answers with an error frame (the HTTP
   route's 404 equivalent), so a dead stop is visible instead of a
   turn that keeps streaming while the client already dropped
   occupancy.

## Queue vs interrupt (Claude Code)

Claude Code: type+Enter while a turn is running = **queue**. Esc/stop
interrupts the live turn and then immediately sends what was queued.

Vercel AI SDK: `stop` aborts the request **now** and forwards abort to
the model. Do not wait for the next token.

OpenProgram copies both. The send queue already parks a typed message
while `runningTask` is set. Stop must:

1. Send `execution.cancel` for the canonical execution.
2. Patch the live assistant to `cancelled` (keep streamed text).
3. `setRunningTaskFor(sessionId, null, "always")` so the queue drains
   at 0ms.

Leaving `cancelling: true` on the running task, or `drain: "never"`,
locks the composer and waits for `running_task_clear`. That is the
old bug.

## Occupancy is released on cancel intent

The session slot is `_running_tasks`, and the canonical driver owns the
active attempt. The transport slot is not a second lifecycle authority.

When `cancel_execution` succeeds:

- Match the admitted `execution_id` to `session_id` + `msg_id`.
- Call `_finish_owned_run(session_id, msg_id)` to remove the transport slot.
- Broadcast `running_task_clear` so every client matches. The
  clear names the finished turn (`msg_id` / `execution_id`).

A late clear or cancelled/result frame for the old turn must
**not** idle a newer reservation, including the just-sent
placeholder `{ msg_id: "" }`. Honor the clear only when it
matches the slot's execution. An unscoped clear is stale after
stop-and-send.

`_finish_owned_run` no-ops if `msg_id` does not match — a newer
reservation is safe. The cancelled turn's `finally` also calls
`_finish_owned_run`; the second call is a no-op.

The canonical Agent driver owns the cooperative cancel event and exact
attempt generation. Transport reservations do not claim a `run_control`
token or derive an execution identifier from `{msg_id}_reply`.

Do **not** wait for the old driver thread to return before the next
`_try_reserve_run`.

## 0ms UI

The 0ms rule already says: Stop clears `runningTask` and patches the
assistant at 0ms. Stop also drains the send queue at that same instant.

Late `running_task` / `stream_event` frames for the cancelled
`execution_id` must not revive the task or append more tokens. The
message status `cancelled` (or `cancelling`) is the guard.

Once `runningTask` is null, `showStop` / `isCancelling` are false and
send is enabled. That is intended. Do not disable send for the old
unwind.

## HTTP abort — do not wait for the next token

Anthropic and OpenAI Completions used to `async for` the SDK stream
and only then check the cancel signal. Mid-reasoning that is seconds.

`iter_until_cancelled` polls the cancel signal every ≤250ms while
**one** `__anext__` stays outstanding. Do not `wait_for` the next
chunk: a timeout cancels that read and kills httpx/OpenAI SSE
iterators. Grok thinking often has no chunk for longer than 250ms;
that finalized a completed assistant with empty content.
On cancel it exits so the `async with` closes the HTTP stream.

`finish_reason` is the end of the turn. Do not wait forever for
`[DONE]` or a trailing `include_usage` chunk — Grok/xAI often leave
the SSE open after the last token, which looks like "still answering"
with the text already on screen. Drain usage for a short window
(`USAGE_DRAIN_S`), then emit `EventDone`.

## 4s grace is for real child processes

`CANCEL_GRACE_S = 4.0` stays. It applies when the owner has a
subprocess or a terminate hook that kills a child (tools, process
runners). Token-only chat owners — no process, no child-killing
terminate — must not wait another 4s after cancel intent. Cooperative
cancel + HTTP abort is enough; occupancy is already released.
