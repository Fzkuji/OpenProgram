# User input requests: pausing a run to ask the user

> This document describes how a running function pauses to ask the user a
> question and resumes with the answer: the API surface, the pending-question
> registry, the transport that carries a question out of a subprocess, and how
> the answer travels back.
> Companion: [../cli/tui-upgrade.md](../../cli/tui-upgrade.md) (TUI surface).

## Problem

A function (especially an `@agentic_function`) sometimes needs the user
mid-run: confirm a destructive step, pick between alternatives, supply a
missing value. That requires pausing execution, surfacing the question in
web/TUI/channels, and resuming with the answer.

## Pre-existing mechanisms

Three related mechanisms exist in the codebase, none of them alive end-to-end
on the main chat path:

| Mechanism | State |
|---|---|
| `ask_user` / `set_ask_user` / `FollowUp` (`openprogram/programs/workflow/ask_user/`) | Primitive complete, DAG awaiting-node bookkeeping complete; but no handler registered in the worker, and the agentic subprocess bridge is one-way — returns `None` in practice |
| webui follow-up round-trip (`webui/server.py:234-270`, WS `follow_up_answer` action, web `handleFollowUpQuestion`) | All three segments exist; the initiator `_web_follow_up` has no caller left — dead code. The web UI side is legacy DOM injection into `#runtime_pending` (only exists while a runtime block streams). TUI types the envelope but never handles it |
| Approval gate (`openprogram/agent/permissions/approval.py`, wired in dispatcher) | Wait machinery complete and live, but `resolve()` is only called from tests; no web/TUI UI; default `bypass` masks it; sub-agents force bypass to avoid 300s hangs |

The skeleton — blocking queue, WS action, stop-sentinel unblocking, DAG
awaiting nodes — is therefore already available. What this design adds is the
registry shape (per-request, not a global handler slot), the subprocess answer
channel, real frontend UI, and explicit timeout semantics.

The binding constraint is that `@agentic_function` bodies run in a **spawned
subprocess** (`agent/process_runner.py`) whose mp.Queue is child→parent only.
Any design must add a parent→child answer queue; no amount of worker-side
wiring avoids this.

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

## API

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

- `ask_user(question)` is a thin alias of `runtime.ask(question)`. With no
  global handler installed it falls back to `runtime.ask`, and
  UserDeclined/AskTimeout collapse to `None` to preserve the older semantics;
  the CLI's `set_ask_user` path is unaffected.
- The `clarify` built-in tool (LLM-callable) works through the same path.
- Three explicit outcomes: answered / declined / timeout. There is no silent
  `None` return after 300 s.
- `runtime.form(...)` (MCP-elicitation-style flat schema) is deferred.

## Mechanism

1. **Registry** (worker process): `PendingQuestion {id, session_id, kind,
   prompt, options, multi, allow_custom, created_at, expires_at}` + a
   per-request `threading.Event`. This replaces the global `set_ask_user`
   handler slot, which two concurrent sessions could overwrite for each
   other. Resolve is an atomic claim-once; `handle_stop` puts the cancel
   sentinel exactly like the existing follow-up queues.
2. **Protocol**: WS broadcast `question.asked / question.replied /
   question.rejected`; REST `GET /api/questions?session_id=` + `POST
   /api/questions/{id}/reply` / `.../reject` for reconnect recovery
   (`webui/routes/questions.py`). `handle_load_session` replays
   still-pending `question.asked` frames on (re)connect. This reuses the
   existing `_broadcast_chat_response` plumbing, whose post-stop silence is
   the behavior we want.
3. **Subprocess bridge**: which channel a question travels on is a
   `QuestionTransport`, shaped like a Python logging Handler (`publish`
   corresponds to `Handler.emit`): `EventLayerTransport` (default — event
   layer to frontend card and bus, used by the worker) and `QueueTransport`
   (back to the parent process over mp.Queue, used by the subprocess). The
   runtime holds its transport explicitly (`runtime._question_transport`)
   rather than through a module-level global switch.
   `run_agentic_in_subprocess` adds a parent→child `answer_queue`;
   `_child_entry` installs `QueueTransport` on the subprocess runtime
   (questions travel up over `event_queue` tagged `__op_question__`) and
   starts an answer-pump thread that takes answers off `answer_queue` and
   resolves the subprocess-local registry. In the parent, `_drain`
   intercepts that envelope, and `_bridge_question_to_parent` registers the
   same qid in the parent registry, emits the frontend card, and starts a
   waiter; a WS reply resolves the parent registry through the existing
   `_resolve_question`, and the waiter pushes the answer back onto
   `answer_queue`. When the subprocess exits or is stopped, the parent
   closes out any remaining pending question as declined and retracts the
   card (claim-once, so a duplicate resolve is harmless).
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
6. **Approval merge**: `permissions/approval.py` moves onto the same registry as
   `kind="approval"`, giving the otherwise unreachable `ask` permission mode
   a real UI, with opencode's reply shape (allow once / always / reject with
   feedback that becomes the tool error text).
7. **Channels**: buttons-as-text-commands (`/answer <id> <choice>`); for
   channel-initiated runs prefer the non-blocking `FollowUp` shape (reply
   ends the turn, user's next message resumes the function) instead of
   holding a thread for 30 minutes.

## Open questions

- Timeout default: 30 min (openclaw) vs shorter for web-first usage.
- Whether `decision.make` should eventually route through the same
  registry when the decision target is the human rather than the model
  (out of scope here, noted for the function-calling unification doc).

## Appendix: Implementation Status

Implemented: the registry, `runtime.ask` / `confirm` / `can_ask`, the three
explicit outcomes (answered / UserDeclined / AskTimeout), the WS
question_reply/reject protocol, the web question card, and stop releasing
pending questions via cancel_session — in `agent/questions.py`,
`agentic_programming/runtime.py`, `webui/ws_actions/session.py`,
`webui/ws_actions/runtime.py`, and `apps/web/components/ui/question-prompt.tsx`.

Reconnect recovery is implemented. A question card is driven only by a live
`question.asked` frame, so after a refresh or a dropped connection that frame
is gone; `handle_load_session` replays every still-pending question of the
session as the same `question.asked` frame, so the frontend redraws with no
changes of its own. REST `GET /api/questions` + `POST
/api/questions/{id}/reply|reject` (`webui/routes/questions.py`) give the same
registry an API-side equivalent, with reply/reject funneling through the same
`_resolve_question` as WS.

The subprocess bridge is implemented, so `@agentic_function` bodies can ask:
`QuestionTransport` (EventLayerTransport / QueueTransport) plus the
parent↔child bridge in `process_runner` (questions up over `event_queue`,
answers back over `answer_queue`) — in `agent/questions.py` (the three
transport classes + `emit_question_asked`), `agentic_programming/runtime.py`
(`set_question_transport`, `_ask_raw` going through
`self._question_transport`), and `agent/process_runner.py` (`answer_queue`,
answer-pump, `_bridge_question_to_parent`, `_decline_bridged_question`).
Covered by `tests/component/agent/test_questions_subprocess_bridge.py` (8 unit tests)
plus a real spawned-subprocess end-to-end check.

Not yet landed: the TUI surface (question/approval prompt in the input slot,
tracked in tui-upgrade.md), and the approval merge, channels, and
`runtime.form` described above.
