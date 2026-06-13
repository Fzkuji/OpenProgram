# User input requests: pausing a run to ask the user

Status: **Phase 1 已落地并验证**（2026-06-13）。runtime.ask/confirm/can_ask、
QuestionRegistry、WS question_reply/reject、前端 QuestionPrompt 卡片全部就位，
端到端（emit→阻塞→答→resume）+ 前端（卡片→点选项→发 reply→收回）双向验证通过。
Phase 2（@agentic_function 子进程桥）、3（TUI）、4（审批合流+channels+form）待做。
Companion: [../cli/tui-upgrade.md](../cli/tui-upgrade.md) (TUI surface).
Research notes:
[user-input-requests-references.md](user-input-requests-references.md)
(full audit of our half-wired mechanisms + Claude Code / opencode /
openclaw / MCP elicitation study).

## Problem

A function (especially an `@agentic_function`) sometimes needs the user
mid-run: confirm a destructive step, pick between alternatives, supply a
missing value. Today there is no working way to pause execution, surface
the question in web/TUI/channels, and resume with the answer.

## What already exists (audit conclusion)

Three related mechanisms, none alive end-to-end on the main chat path:

| Mechanism | State |
|---|---|
| `ask_user` / `set_ask_user` / `FollowUp` (`openprogram/functions/agentics/ask_user/`) | Primitive complete, DAG awaiting-node bookkeeping complete; but no handler registered in the worker, and the agentic subprocess bridge is one-way — returns `None` in practice |
| webui follow-up round-trip (`webui/server.py:234-270`, WS `follow_up_answer` action, web `handleFollowUpQuestion`) | All three segments exist; the initiator `_web_follow_up` lost its only caller in `b39347fb` — dead code. Web UI side is legacy DOM injection into `#runtime_pending` (only exists while a runtime block streams). TUI types the envelope but never handles it |
| Approval gate (`openprogram/agent/_approval.py`, wired in dispatcher) | Wait machinery complete and live, but `resolve()` is only called from tests; no web/TUI UI; default `bypass` masks it; sub-agents force bypass to avoid 300s hangs |

So the skeleton (blocking queue, WS action, stop-sentinel unblocking, DAG
awaiting nodes) is all there. What's missing is the registry shape
(per-request, not a global handler slot), the subprocess answer channel,
real frontend UI, and honest timeout semantics.

Key constraint discovered: `@agentic_function` bodies run in a **spawned
subprocess** (`agent/process_runner.py`) with an mp.Queue that is
child→parent only. Any design must add a parent→child answer queue; no
amount of worker-side wiring avoids this.

## Reference designs (what we take)

- **opencode**: tool calls `ctx.ask(...)` → server-side Deferred + pending
  map → event `permission.asked` down, REST reply up, **plus a list
  endpoint** so a reconnecting client can recover pending questions. Reject
  may carry a message that becomes the tool-error text the model sees.
- **Claude Code**: AskUserQuestion rides the permission pipeline; options +
  always-present "Other" free-text; pending request *snapshot* persisted in
  session metadata so remote UIs can redraw it (execution stack never
  persisted); tools that require interaction are disabled when no human is
  attached.
- **openclaw**: 30-min timeout with an explicit fallback (never silent);
  channel buttons whose value is a plain text command (`/approve <id> …`)
  so text-only channels work identically; for channel-initiated runs, the
  tool returns "pending" immediately and the result is re-injected later
  (non-blocking mode).
- **MCP elicitation**: the three-outcome protocol — accept / decline /
  cancel — and flat-object schema constraints for form-style asks.

All four implement "execution point blocks on a primitive, UI resolves it"
— no generator/coroutine acrobatics. Ours blocks a thread (functions
already run in threads/subprocesses).

## API (the part to agree on)

On `runtime`, next to `runtime.exec` / `decision`:

```python
# Inside any @agentic_function / @function body
answer = runtime.ask(
    "Which library for date formatting?",
    options=["dayjs", "date-fns", "luxon"],  # optional; None = free text
    multi=False,                # True -> returns list[str]
    allow_custom=True,          # free text allowed besides options
    timeout=1800,               # seconds, default 30 min
    default=None,               # returned on timeout; no default -> AskTimeout
)
# -> str (or list[str]); user pressing Decline raises UserDeclined

ok = runtime.confirm("Archive all 87 emails?", detail=preview,
                     timeout=600, default=False)  # -> bool, never raises on timeout

runtime.can_ask()  # -> bool; False in headless runs so authors can branch
```

- `ask_user(question)` stays as a thin alias of `runtime.ask(question)`.
- The `clarify` built-in tool (LLM-callable) starts working again for free.
- Three explicit outcomes (answered / declined / timeout) — the current
  "300 s silently returns None" behavior is removed.
- `runtime.form(...)` (MCP-elicitation-style flat schema) is deferred.

## Mechanism

1. **Registry** (worker process): `PendingQuestion {id, session_id, kind,
   prompt, options, multi, allow_custom, created_at, expires_at}` + a
   per-request `threading.Event`. Replaces the global `set_ask_user`
   handler slot (fixes the concurrent-session overwrite bug). Resolve is
   atomic claim-once; `handle_stop` puts the cancel sentinel exactly like
   the existing follow-up queues.
2. **Protocol**: WS broadcast `question.asked / question.replied /
   question.rejected`; REST `POST /api/questions/{id}/reply`, `.../reject`,
   and `GET /api/questions?session_id=` for reconnect recovery. Reuses the
   existing `_broadcast_chat_response` plumbing (its post-stop gag is the
   behavior we want).
3. **Subprocess bridge**: second mp.Queue (parent→child) in
   `process_runner.run_agentic_in_subprocess`; `_child_entry` registers a
   handler that emits the question envelope through the existing event
   queue and blocks on the answer queue. Parent drain thread registers the
   pending entry and routes answers back by request id.
4. **Persistence**: persist the request snapshot, not the execution stack.
   The DAG already writes `status="awaiting"` user-role nodes; on worker
   restart, leftover pendings are marked expired and DAG nodes
   `unanswered`. No durable-execution resume (all four references
   deliberately skip it).
5. **Frontends**: web gets a React question card in the message stream
   (replacing the legacy DOM injection) and the composer doubles as the
   answer box while a question is pending; TUI renders the question in the
   input slot (tui-upgrade.md P2). First answer wins across surfaces;
   `question.replied` retracts the UI elsewhere.
6. **Approval merge (later phase)**: `_approval.py` migrates onto the same
   registry as `kind="approval"`, giving the dead `ask` permission mode a
   real UI, with opencode's reply shape (allow once / always / reject with
   feedback that becomes the tool error text).
7. **Channels (later phase)**: buttons-as-text-commands (`/answer <id>
   <choice>`); for channel-initiated runs prefer the non-blocking
   `FollowUp` shape (reply ends the turn, user's next message resumes the
   function) instead of holding a thread for 30 minutes.

## Phases

- **Phase 1 — minimal live path** ✅（2026-06-13 落地）: registry +
  `runtime.ask`/`confirm`/`can_ask` + WS question_reply/reject 协议 + web
  question card。三态显式（answered / UserDeclined / AskTimeout）替代旧的
  300s 静默 None；stop 时 cancel_session 解除待答。as-built：
  `agent/questions.py`（registry）、`agentic_programming/runtime.py`
  （ask/confirm）、`webui/ws_actions/session.py`（reply/reject handler）、
  `webui/ws_actions/runtime.py`（stop 解除）、`web/components/ui/question-prompt.tsx`
  （卡片）。REST list/reply 端点（reconnect 恢复）延到后续单元。
- **Phase 2 — subprocess bridge**: `@agentic_function` bodies can ask
  (the actual headline use case).
- **Phase 3 — TUI surface**: question/approval prompt in the input slot
  (tracked in tui-upgrade.md).
- **Phase 4 — approval merge + channels + `runtime.form`**.

## Open questions

- Timeout default: 30 min (openclaw) vs shorter for web-first usage.
- Whether `decision.make` should eventually route through the same
  registry when the decision target is the human rather than the model
  (out of scope here, noted for the function-calling unification doc).
