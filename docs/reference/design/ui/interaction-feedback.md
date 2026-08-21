# Interaction feedback — the 0ms rule

Every user click that starts something slower than
~100ms gets INSTANT visible feedback. An optimistic transitional state renders
immediately (0ms, client-side); real data backfills when it arrives; failures
roll back with a visible error.

Never let a click sit with no visible change while a round-trip is in flight.

## The three layers

1. **0ms optimistic** — the click flips a visible transitional state on the
   client, before any network I/O. Spinner card, target-version highlighted,
   "stopping…", a pending bubble in the transcript. Written straight into the
   session store (an optimistic flag / status patch on the message or store
   object — never parallel bookkeeping).
2. **Fast server confirm** — the backend acknowledges. For function runs the
   dispatcher pre-creates the run's node at dispatch, so a
   `load_session` ~0.13s after the click returns the real pending card and
   `chat_ack {function_run:true}` triggers immediate hydration. The hydrate's
   `setMessages` replaces the whole transcript, so a client placeholder keyed
   with a throwaway id is dropped cleanly — no flicker.
3. **Streaming backfill** — `tree_update` / `stream_event` deltas fill the card
   live; the terminal `result` / `running_task_clear` finalizes it.

**Failure rollback (required):** every optimistic state must resolve. Either
the backfill supersedes it, or a timeout (10s for control actions,
`OPTIMISTIC_TIMEOUT_MS`) reverts the state and shows an error toast. An
optimistic state that can hang forever is a lie — worse than no feedback.

## Shared helper

`apps/web/lib/runtime-bridge/optimistic-action.ts` — `optimisticAction({apply,
settled, revert, onTimeoutMessage})`. `apply` flips the 0ms state; `settled()`
returns true once real data supersedes it (message gone from the store, tree
repopulated, `branch.active` flipped…); on timeout with `settled()` still
false it runs `revert` + toasts. Used by the surfaces whose confirm path is a
`load_session` reload (retry, version switcher). Surfaces with a purely local
transient (stop, fn-form placeholder, branch checkout) inline the pattern.

## Per-surface behaviour

| Surface | First 100ms after the click |
|---|---|
| Chat send | welcome hides at 0ms; user bubble + reply placeholder land on `chat_ack` (~1 round trip) |
| Stop button | runningTask cleared and the assistant message patched to `[cancelled by user]` at 0ms; the send queue drains at the same instant |
| Function-call Retry | card flips to a spinner body + "running", switcher → N+1/N+1, at 0ms; reload backfills; 10s revert |
| fn-form / welcome submit | a pending runtime card is inserted into the transcript at 0ms; hydration replaces it seamlessly; a POST failure removes it and toasts |
| Runtime `< N/M >` switcher | current card → spinner body + target sibling index at 0ms; reload swaps content; 10s revert |
| Chat message `< N/M >` switcher | the `N/M` label advances to the target at 0ms; reload backfills; failure reverts the label and toasts |
| Branches panel checkout | the clicked row is highlighted active at 0ms; the real `branch.active` supersedes it; 10s self-clear |
| Chat Retry / Edit / Branch / Rewind | `setBusy(true)` dims the buttons and `setRunActive` greys Edit/Retry — the busy flag is the transitional feedback |
| Session switch (sidebar) | `router.push`; the store already holds the prior messages, so cached messages render on route change |
| Enable/disable models · tools · toggles | local store flip, instant |

New optimistic states use the store's existing machinery (`updateMessage`
status/tree patches, `siblingIndex`, `setRunningTaskFor`, `appendMessage`
placeholder). No parallel state.
