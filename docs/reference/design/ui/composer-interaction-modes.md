# Composer interaction modes — the input box as the unified catch point for "user decisions"

## In one sentence

The chat input box (composer) is not just a text field but a **container that switches between several shapes**.
Each shape is a "transformation" (mode): filling in a function form is one, answering a runtime.ask question
is another, approving a tool is another. Every interaction that **requires a user decision** is presented by transforming in place inside the input box,
rather than each popping its own floating window. Each mode has its own folder and follows the same interface,
so any new interaction either directly reuses an existing mode or derives from one.

## Why one catch point

Three interactions put the turn back on the user: running an `@agentic_function`,
answering a `runtime.ask` question, and approving a tool execution. Presented
separately — one as an in-place form, one as a floating card, one with no UI at
all — the user's attention is dragged to different places and each path is coded
on its own. Presenting all three in the input box keeps the user's gaze in the
input area and gives the frontend a single catch point for user decisions.

This also aligns with the event layer: the event layer is a unified event stream, and any "requires a user decision" event (question.asked,
the future approval.asked / form.asked) should land on the frontend at **the same exit** — the input box's
mode container, which picks a transformation to present it. One backend registry (QuestionRegistry), one
frontend catch point (composer), one event path.

## fn-form: the template for a transformation

fn-form is the reference shape; the mode framework makes its conventions explicit
so more modes can follow them:

* **Trigger state in the store**: `session-store.ts`'s `fnFormFunction` (+ `fnFormClosing`),
  `openFnForm(fn)` / `closeFnForm()`. Non-empty = currently in fn-form shape.
* **Field state in a hook**: `use-fn-form-state.ts` (values / workdir / error / closing),
  reseeds defaults when fn changes.
* **Visuals in module.css**: `inputWrapper` gets `morphed` to change shape; `outgoingLayer`
  does the cross-fade animation when switching fn→fn.
* **Send button behavior switches with it**: `onSendButtonClick = fnFormActive ? submitFnForm : submit`,
  with disabled / title also changing with the current shape.
* **Components**: `fn-form/fn-form.tsx` (the shape) + `fn-form-fields.tsx` (field rendering).

`runtime.ask` follows the same conventions as a `question` mode rather than as a
standalone floating popup.

A function invocation has two frontend inputs. Selecting a registered function
opens `FunctionForm`; entering an exact, complete expression such as
`gui_agent(task="Verify the title", surface="browser", max_steps=3)` in the idle
composer invokes the same function without opening the form. The expression is
recognized only when the entire trimmed input is one registered
`name(parameter=literal, ...)` call. Both inputs are normalized and schema-validated,
then passed to the same `FunctionInvocation` dispatcher. Explanatory text that
contains a call, and call text wrapped in inline or fenced backticks, remain
ordinary chat input.

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

### Layout in a morphed mode

The composer keeps the three-band arrangement in every mode: env chips above,
the wrapper box, and one detached controls row (permission / models / effort /
context ring) below. Morphing only grows the wrapper upward — the controls row
never moves or restyles. Inside the wrapper a morphed mode is a 48px header plus
a body with 12px padding on every side:

* **fn-form**: the run button (the same 24px square as the chat send button) and
  the 24px close button sit side by side at the header's right edge; the body
  ends right after the last field.
* **question / approval**: the header holds the badge + progress dots with the
  "Chat about this" button at its right edge; the nav buttons (‹ Previous /
  Next › / Send) are the body's **last row**, right-aligned in normal flow — no
  absolute positioning, no reserved bottom band.

Buttons are rounded rectangles (6px radius) throughout — no pill or circle
shapes. A static size-accurate mock of this layout lives in
[fn-form-compact-mock.html](fn-form-compact-mock.html).

### The unified interface of a mode

Each mode is a **self-contained unit** that exposes the same contract to the container:

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
apps/web/components/chat/composer/
  modes/
    index.ts            # mode registry (id → ComposerMode), the container looks up against this
    types.ts            # the ComposerMode interface
    fn-form/            # the function parameter form
    question/           # runtime.ask
    approval/           # tool approval (a derivative of question)
  index.tsx             # container: reads the current mode, looks it up, renders Body + takes over Send
```

Subsequent derivatives: `approval/` directly imports `question/`'s Body and wraps another layer around it (adding the dangerous summary),
which is "deriving from an existing transformation".

## Three communication shapes: direct store / request / broadcast (don't mix them)

A mode is defined by how it is triggered and how the user's action is returned. These go through **three
different channels**, distinguished by one rule: **only consider the bus when crossing a process/network boundary; a state change in the same place
changes the store directly; to have the backend do one thing and give a definite reply, use a request (HTTP/RPC), not a broadcast.**

| Shape | Channel | Example | Why |
|---|---|---|---|
| **Direct store** | Zustand action, frontend state in the same place | Click a sidebar function → `openFnForm(fn)` enters fn-form | Crosses no boundary, the frontend just changes its own shape; putting it on the bus is overkill |
| **Request (command)** | HTTP POST / RPC, one round trip | FunctionForm or an exact function expression → `POST /api/function/{name}`; mode reply → `question_reply` WS action | "I want you to do something + give me a reply" (returns `session_id` after running, resolves which question). The request/response model fits best; the bus is fire-and-forget and can't get the reply back |
| **Broadcast (event)** | event bus → WS | function run progress/output/`question.asked`/`file.changed` | The backend one-directionally emits "what happened"; whoever cares listens, no waiting for a reply |

**Follow a function invocation end to end**: either select the function (direct
store, open FunctionForm, fill parameters) or enter an exact registered
expression (parse and validate in the idle composer). Both paths produce the
same `FunctionInvocation` and call the shared dispatcher (**request**: POST
initiates and returns `session_id`) without calling the chat-message sender or
creating a user chat turn. The function then runs in a subprocess; run events
and mid-flight `runtime.ask` updates return through the event layer → WS
(**broadcast**).

Initiating a run is never a fire-and-forget bus event: that loses the
`session_id`, leaving the frontend with no way to navigate to or bind the
session. A request initiates; a broadcast reports the process.

## How events route in

> This section only covers the third row of the table above — **broadcast**: how "requires a user decision" events emitted by the backend through the event layer
> land on the composer. The **reply** after the user acts is the second row (request/WS action), see the table above.

The backend sends "requires a user decision" frames through the event layer as
`question.asked`, which approval also uses. On the frontend:

1. `use-ws.ts` receives `question.asked` and writes the envelope into the store's
   `pendingDecision`.
2. The composer container subscribes to `pendingDecision`: if non-empty, it picks a mode based on `kind`
   (`ask`/`confirm` → question, `approval` → approval) and enters that shape.
3. The user acts in the input area → the mode's primary/secondary action sends `question_reply` /
   `question_reject`; the backend collects them at `_resolve_question`.
4. Answered elsewhere first, or stopped → the backend broadcasts `question.replied`/`rejected` → the frontend clears
   `pendingDecision` and exits the mode.

**Mutual exclusion and priority**: only one mode is presented at a time, under two rules —

* **Queue among system decisions**: two "the system needs a user decision" events (e.g. question arrives first, then
  approval) → FIFO queue, one at a time. After the previous one is answered, the next is presented automatically. No stacking,
  no side-by-side.
* **System decision vs a mode the user actively opened**: a fn-form the user opened themselves collides with a system
  decision → directly **cancel** the fn-form (the user opened it actively, discarding it doesn't matter), letting the system decision occupy
  the input area. No stashing, no restoring.

`pendingDecision` is therefore a FIFO queue: a non-empty head occupies the input area, a new system decision is enqueued,
and a user-opened fn-form on screen is cleared before the head is shown. No stack, no snapshot.

## Backend: approval merged into QuestionRegistry

So that approval travels the same event path to the same composer catch point, approval lives in
`QuestionRegistry` rather than in a registry of its own (user-input-requests.md, point 6):

* `await_user_approval` registers a `kind="approval"` PendingQuestion
  (prompt = "Allow executing {tool}?", options = ["Allow", "Reject"], detail = parameter summary),
  and sends `question.asked` through the event layer.
* The async wait reuses `asyncio.to_thread(ev.wait, timeout)` (the tool execute is a coroutine,
  it can't synchronously block the loop).
* The boolean result is mapped from the question's three states: answered "Allow" → True; declined / timeout → False.
* `approval_registry()` returns the unified QuestionRegistry; there is no separate
  `ApprovalRegistry` and no custom `approval_request` envelope.

One registry, one event (`question.asked`), one frontend catch point.

## Presentation decisions

* **approval dangerous summary**: the full command/parameters are shown, truncated
  head-and-tail when too long. No dangerous-token highlighting.
* **Reject with reason**: approval's secondary action accepts a text reason, and that
  reason becomes the tool error text returned to the model.
* **Timeout**: a mode waiting on the user finishes as declined on timeout, exits
  automatically, and leaves a one-line note in the input area ("no response, timed out").

## Appendix: Implementation Status

The modes framework, the question mode, the backend approval merge, the approval
mode with rejection-by-reason, and FIFO conflict queueing with timeout reclaim are
all implemented. fn-form is still rendered inline by the composer rather than
through the modes registry; folding it in is a pending cleanup that does not
change behaviour.

## Related

* user-input registry / runtime.ask: [../runtime/user-input-requests.md](../runtime/operations/user-input-requests.md)
* event layer (the unified event stream, this is its alignment landing point in the frontend):
  [../proactive/event-reference.html](../proactive/event-reference.html)
