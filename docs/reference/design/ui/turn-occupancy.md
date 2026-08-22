# Turn occupancy — stop, queue, and the session slot

A session admits **one live turn**. Typing while it runs is a queue, not
a second turn. Stop is an interrupt: occupancy is released on cancel
*intent*, not when the old turn thread dies.

Related: [`interaction-feedback.md`](interaction-feedback.md) (0ms UI),
[`send-queue-reliability.html`](send-queue-reliability.html) (queue
mechanics). Code: `use-chat-submit.ts` (`stopSession`),
`server.py` (`_finish_owned_run` / `_try_reserve_run`),
`run_control.py` (`cancel_execution`, `CANCEL_GRACE_S`),
`providers/utils/cancelable_stream.py`.

## Queue vs interrupt (Claude Code)

Claude Code: type+Enter while a turn is running = **queue**. Esc/stop
interrupts the live turn and then immediately sends what was queued.

Vercel AI SDK: `stop` aborts the request **now** and forwards abort to
the model. Do not wait for the next token.

OpenProgram copies both. The send queue already parks a typed message
while `runningTask` is set. Stop must:

1. Send `execution.cancel` (or the `stop` fallback).
2. Patch the live assistant to `cancelled` (keep streamed text).
3. `setRunningTaskFor(sessionId, null, "always")` so the queue drains
   at 0ms.

Leaving `cancelling: true` on the running task, or `drain: "never"`,
locks the composer and waits for `running_task_clear`. That is the
old bug.

## Occupancy is released on cancel intent

The session slot is `_running_tasks` **and** the active runtime.
`_is_run_active` still returns True via `_has_active_runtime` if only
the task map is popped.

When `cancel_execution` succeeds:

- Map `execution_id` to `session_id` + `msg_id` (chat executions are
  `{msg_id}_reply`).
- Call `_finish_owned_run(session_id, msg_id)` so both the task entry
  and the runtime unregister.
- Broadcast `running_task_clear` so every client matches.

`_finish_owned_run` no-ops if `msg_id` does not match — a newer
reservation is safe. The cancelled turn's `finally` also calls
`_finish_owned_run`; the second call is a no-op.

Also retire that execution's cancel token. Occupancy is the slot;
the token is the stop flag. Leaving a cancelled token in
`_current_tokens` makes the next `claim_cancel_event` fail, or a
reused `{msg_id}_reply` start already cancelled. The old stream
still sees its own Event (`opts.signal`), which is already set.

Do **not** wait for `process_user_turn` to return before the next
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
