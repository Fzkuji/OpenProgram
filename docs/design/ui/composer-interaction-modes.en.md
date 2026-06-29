# Composer interaction modes — the input box as the unified catch point for "user decisions"

Status: **Shipped** (2026-06-14). All five steps implemented and self-verified in the browser, committed step by step.
as-built: modes framework skeleton (26685949) → question mode + removal of the floating popup (bc144b8c) →
backend approval merge (73a094be) → approval mode + rejection with reason (27c05faa) → conflict queueing +
timeout reclaim (c0c8956e). The only deviation from the original design: the first version of fn-form is still
rendered inline by the composer (it was the original template for "input-box transformation" to begin with), and was not formally
placed into the modes registry — question / approval go through registry-style mode components; folding fn-form into the registry is left as a follow-up cleanup (non-blocking).

## In one sentence

Upgrade the chat input box (composer) from "can only type" into a **container that can switch between several shapes**.
Each shape is a "transformation" (mode): filling in a function form is one, answering a runtime.ask question
is another, approving a tool is another. Every interaction that **requires a user decision** is presented by transforming in place inside the input box,
rather than each popping its own floating window. Each mode has its own folder and follows the same interface,
so any new interaction either directly reuses an existing mode or derives from one.

## Why do it this way

Right now the frontend has two mutually inconsistent ways of presenting "the user needs to act":

| Interaction | How it's presented today | Paradigm |
|---|---|---|
| Running an @agentic_function | The input box **transforms in place into a parameter form** (fn-form), Send becomes "Run" | input-box transformation ✅ |
| runtime.ask asking a question | A **standalone floating card** in the middle of the screen (question-prompt.tsx, portaled to body) | floating popup ❌ |
| Tool approval (permission ask) | **No UI** (dead in production, only tests resolve it in the background) | none ❌ |

Three interactions where it's "the user's turn": one transforms in place, one floats, one is missing. The user's attention gets
dragged to different places, and the code is written separately for each. **Unify into one place**: every "it's the user's turn" happens in the input box,
the user's gaze never leaves the input area; the frontend has a single catch point for "user decisions".

This also aligns with the event layer: the event layer is a unified event stream, and any "requires a user decision" event (question.asked,
the future approval.asked / form.asked) should land on the frontend at **the same exit** — the input box's
mode container, which picks a transformation to present it. One backend registry (QuestionRegistry), one
frontend catch point (composer), one event path.

## Current state: fn-form is already the template for a "transformation"

Reading `web/components/chat/composer/` gives the facts — fn-form already got "input-box transformation"
right, and the new framework makes its implicit conventions explicit and accommodates more modes:

* **Trigger state in the store**: `session-store.ts`'s `fnFormFunction` (+ `fnFormClosing`),
  `openFnForm(fn)` / `closeFnForm()`. Non-empty = currently in fn-form shape.
* **Field state in a hook**: `use-fn-form-state.ts` (values / workdir / error / closing),
  reseeds defaults when fn changes.
* **Visuals in module.css**: `inputWrapper` gets `fnFormMode` to change shape; `outgoingLayer`
  does the cross-fade animation when switching fn→fn.
* **Send button behavior switches with it**: `onSendButtonClick = fnFormActive ? submitFnForm : submit`,
  with disabled / title also changing with the current shape.
* **Components**: `fn-form/fn-form.tsx` (the shape) + `fn-form-fields.tsx` (field rendering).

The question (runtime.ask) doesn't go through this today; it's a standalone floating popup (`web/components/ui/question-prompt.tsx`,
listens for the `op:question-asked` window event, sends `question_reply`/`question_reject`).
This design retires it and turns it into a mode.

## Model: container + transformation (mode)

### Container (composer)

At any moment the composer is in **one** mode:

* `idle` — normal typing (default).
* `fn-form` — filling in a function parameter form.
* `question` — answering a runtime.ask (options / multi-select / free text).
* `approval` — approving/rejecting a tool execution (a derivative of question: two fixed options +
  a dangerous-action summary).
* Future: `form` (runtime.form multi-field), `diff-approve` (approval with a diff preview)…

Only one mode occupies the input area at a time (mutually exclusive). Mode switching goes through the container's state machine; entering/exiting
both have in-place transformation animations (reusing `outgoingLayer` cross-fade).

### The unified interface of a mode

Each mode is a **self-contained unit** that exposes the same contract to the container (draft, to be finalized at implementation time):

```ts
interface ComposerMode<TState> {
  id: string;                       // "fn-form" | "question" | "approval" | …
  // the data this mode needs to enter (fn definition / question envelope / approval request)
  // pushed into the store by the trigger source; the container reads it out and passes it to the mode.
  useModeState(input): TState;      // the local-state hook of this mode (e.g. use-fn-form-state)
  Body: React.FC<{ state: TState; ... }>;   // the main body rendered in the input area
  // the behavior/text/availability of the primary action button (which takes over the composer's Send slot)
  primaryAction(state): { label; disabled; run: () => void };
  // the secondary action (cancel/reject), exits the mode
  secondaryAction(state): { label; run: () => void } | null;
  onExit?(): void;                  // cleanup (clear state, send an unanswered signal, etc.)
}
```

The container only knows this interface; adding a mode = adding a folder that implements it, **without changing the container itself**.

### File organization

```
web/components/chat/composer/
  modes/
    index.ts            # mode registry (id → ComposerMode), the container looks up against this
    types.ts            # the ComposerMode interface
    fn-form/            # the existing fn-form migrated in, as the first mode
    question/           # runtime.ask (absorbs question-prompt's logic, drops the floating popup)
    approval/           # tool approval (a derivative of question)
  index.tsx             # container: reads the current mode, looks it up, renders Body + takes over Send
```

Subsequent derivatives: `approval/` directly imports `question/`'s Body and wraps another layer around it (adding the dangerous summary),
which is "deriving from an existing transformation".

## Three communication shapes: direct store / request / broadcast (don't mix them)

Each mode is about "how it's triggered to enter, and how the user's action is returned afterward". Reading the code you'll find they go through **three
different channels**, distinguished by a one-sentence rule: **only consider the bus when crossing a process/network boundary; a state change in the same place
changes the store directly; to have the backend do one thing and give a definite reply, use a request (HTTP/RPC), not a broadcast.**

| Shape | Channel | Example | Why |
|---|---|---|---|
| **Direct store** | Zustand action, frontend state in the same place | Click a sidebar function → `openFnForm(fn)` enters fn-form | Crosses no boundary, the frontend just changes its own shape; putting it on the bus is overkill |
| **Request (command)** | HTTP POST / RPC, one round trip | fn-form clicks "Run" → `POST /api/function/{name}`; mode reply → `question_reply` WS action | "I want you to do something + give me a reply" (returns `session_id` after running, resolves which question). The request/response model fits best; the bus is fire-and-forget and can't get the reply back |
| **Broadcast (event)** | event bus → WS | function run progress/output/`question.asked`/`file.changed` | The backend one-directionally emits "what happened"; whoever cares listens, no waiting for a reply |

**Follow fn-form end to end**: click the function (direct store, open the form) → fill in parameters (frontend local state) →
click run (**request**: POST initiates, returns session_id) → the function runs in a subprocess, with dynamic backflow (**broadcast**:
run events / mid-flight `runtime.ask` both go through the event layer → WS). Three segments, three channels, each taking the fittest tool.

Counterexample (to prevent stepping on it later): don't make "initiate a run" a fire-and-forget bus event — then you can't get the
`session_id`, and the frontend has no way to navigate to / bind the session. Use a request to initiate, broadcast for the process.

## How events route in

> This section only covers the third row of the table above — **broadcast**: how "requires a user decision" events emitted by the backend through the event layer
> land on the composer. The **reply** after the user acts is the second row (request/WS action), see the table above.

The backend unchangedly sends "requires a user decision" frames through the event layer (`question.asked`, which approval also goes through after the merge,
see below). Frontend:

1. `use-ws.ts` receives `question.asked` → currently turns it into the `op:question-asked` window event
   for the popup. Change to: write into the store's `pendingDecision` (envelope).
2. The composer container subscribes to `pendingDecision`: if non-empty, it picks a mode based on `kind`
   (`ask`/`confirm` → question, `approval` → approval) and enters that shape.
3. The user acts in the input area → the mode's primary/secondary sends `question_reply` /
   `question_reject` (reusing the existing WS actions, the backend's `_resolve_question` collection point unchanged).
4. Answered elsewhere first / stop → the backend broadcasts `question.replied`/`rejected` → the frontend clears
   `pendingDecision`, exits the mode (reusing the existing "reclaim" logic).

**Mutual exclusion and priority (decided, 2026-06-13)**: only one mode is presented at a time, with two rules —

* **Queue among system decisions**: two "the system needs a user decision" events (e.g. question arrives first, then
  approval) → FIFO queue, one at a time. After the previous one is answered, the next is presented automatically. No stacking,
  no side-by-side.
* **System decision vs a mode the user actively opened**: a fn-form the user opened themselves collides with a system
  decision → directly **cancel** the fn-form (the user opened it actively, discarding it doesn't matter), letting the system decision occupy
  the input area. No stashing, no restoring.

That is: `pendingDecision` is a FIFO queue; when the head is non-empty it occupies the input area. A new system decision is enqueued;
if a user-active mode (fn-form) is showing at the moment, clear it and then show the head. Simple implementation, no stack, no snapshot.

## Backend: approval merged into QuestionRegistry

So that "approval" also goes through the same event path → lands on the same composer catch point, the backend merges
`_approval.py` into `QuestionRegistry` (user-input-requests.md, point 6):

* `await_user_approval` no longer uses a standalone `ApprovalRegistry` + a custom
  `approval_request` envelope, but registers a `kind="approval"` PendingQuestion
  (prompt = "Allow executing {tool}?", options = ["Allow", "Reject"], detail = parameter summary),
  and sends `question.asked` through the event layer.
* The async wait reuses `asyncio.to_thread(ev.wait, timeout)` (the tool execute is a coroutine,
  it can't synchronously block the loop).
* The boolean result is mapped from the question's three states: answered "Allow" → True; declined / timeout → False.
* `ApprovalRegistry` is retired; the `approval_registry()` accessor is kept as a thin shim or its callers migrated;
  the two dispatcher approval tests are rewritten to go through QuestionRegistry.

After the merge: one registry, one event (question.asked), one frontend catch point. Approval is incidentally
**revived** (it had no UI before).

## Retirements (done)

* The `web/components/ui/question-prompt.tsx` floating popup + app-shell mount → deleted.
* `_approval.py`'s `ApprovalRegistry` / `approval_request` envelope → deleted;
  `approval_registry()` returns the unified QuestionRegistry.

## Landing order (each step independently verified, all done)

1. ✅ **Framework skeleton** (26685949): `modes/types.ts` (the ComposerMode interface) +
   `modes/index.ts` (the registry). The first version of fn-form is still inline (see the Status deviation note).
2. ✅ **question mode** (bc144b8c): `modes/question/`, runtime.ask presented in the input box
   in place; remove the floating popup; use-ws → the store's pendingDecisions queue. Verified end to end with a real subprocess.
3. ✅ **Backend approval merge** (73a094be): `await_user_approval` goes through QuestionRegistry
   (kind=approval), sends question.asked through the event layer; ApprovalRegistry retired; the two
   dispatcher approval tests migrated to the unified registry + event bus contract.
4. ✅ **approval mode** (27c05faa): `modes/approval/`, a derivative of question —
   dangerous summary (tool name + parameters) + Allow/Reject + rejection with reason (the reason becomes tool error text).
   Verified end to end with a real subprocess (including the subprocess approval bridge).
5. ✅ **Conflict queueing + timeout reclaim** (c0c8956e): FIFO queue one at a time; a user-active fn-form
   colliding with a system decision is cancelled; on timeout, question.rejected is broadcast through the transport to reclaim the card
   (fixed the real bug of "the timed-out card hanging stuck"). Preemption verified in the browser.

Each step can be self-verified in the browser and committed independently.

## Decisions (decided, 2026-06-13)

* **Same-screen conflict**: FIFO queue; system decisions queue, one at a time; a user-actively-opened fn-form colliding with
  a system decision is cancelled directly (see above).
* **approval dangerous summary**: show the full command/parameters, truncating when too long (keep head and tail); no dangerous-token
  highlighting (the first version keeps it simple).
* **Reject with reason**: approval's secondary allows attaching a text reason, and the reason becomes the tool error
  text returned to the model (opencode's approach).
* **timeout**: a mode occupying the input area while waiting for the user, on timeout finishes as declined, auto-exits the mode, and gives a one-line prompt in the
  input area ("no response, timed out").

## Related

* user-input registry / runtime.ask: [../runtime/user-input-requests.md](../runtime/operations/user-input-requests.md)
* event layer (the unified event stream, this is its alignment landing point in the frontend):
  [../proactive/event-reference.html](../proactive/event-reference.html)
