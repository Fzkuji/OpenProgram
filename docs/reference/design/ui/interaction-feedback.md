# Interaction feedback — the 0ms rule

**Policy (standing):** every user click that starts something slower than
~100ms gets INSTANT visible feedback. An optimistic transitional state renders
immediately (0ms, client-side); real data backfills when it arrives; failures
roll back with a visible error. *"点完之后没有反馈很折磨。"*

Never let a click sit with no visible change while a round-trip is in flight.

## The three layers

1. **0ms optimistic** — the click flips a visible transitional state on the
   client, before any network I/O. Spinner card, target-version highlighted,
   "stopping…", a pending bubble in the transcript. Written straight into the
   session store (an optimistic flag / status patch on the message or store
   object — never parallel bookkeeping).
2. **Fast server confirm** — the backend acknowledges. For function runs the
   dispatcher pre-creates the run's node at dispatch (commit `4712f368`), so a
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

`web/lib/runtime-bridge/optimistic-action.ts` — `optimisticAction({apply,
settled, revert, onTimeoutMessage})`. `apply` flips the 0ms state; `settled()`
returns true once real data supersedes it (message gone from the store, tree
repopulated, `branch.active` flipped…); on timeout with `settled()` still
false it runs `revert` + toasts. Used by the surfaces whose confirm path is a
`load_session` reload (retry, version switcher). Surfaces with a purely local
transient (stop, fn-form placeholder, branch checkout) inline the pattern.

## Per-surface inventory

| Surface | First 100ms BEFORE | First 100ms AFTER | Status |
|---|---|---|---|
| Chat send | welcome hides (0ms); user bubble + reply placeholder land on `chat_ack` (~1 RT) | unchanged — send already optimistic enough; bubble on ack | OK |
| Stop button | runningTask cleared + assistant patched to `[cancelled by user]` at 0ms | unchanged | OK (already optimistic) |
| **Function-call Retry** | nothing — card stays on old version until run completes + reload | card flips to spinner body + "running", switcher → N+1/N+1, at 0ms; reload backfills; 10s revert | **DONE** |
| **fn-form / welcome submit** | welcome hides + run flag flips; blank transcript gap until ~0.13s hydrate | pending runtime card inserted into transcript at 0ms; hydrate replaces it seamlessly; POST failure removes it + error toast | **DONE** |
| **Runtime `< N/M >` switcher** | POST checkout → load_session; no visual change until reload | current card → spinner body + target sibling index at 0ms; reload swaps content; 10s revert | **DONE** |
| **Chat message `< N/M >` switcher** | `busy` dims buttons; label unchanged until reload | `N/M` label advances to target at 0ms; reload backfills; failure reverts label + toast | **DONE** |
| **Branches panel checkout** | WS checkout + load_session; row highlight unchanged until reload | clicked row highlighted active at 0ms; real `branch.active` supersedes; 10s self-clear | **DONE** |
| Chat Retry / Edit / Branch / Rewind | `setBusy(true)` dims buttons; `setRunActive` greys Edit/Retry | unchanged — busy flag is adequate transitional feedback | OK (adequate) |
| Session switch (sidebar) | `router.push`; cached messages render on route change | unchanged — store already holds prior messages | OK |
| Enable/disable models · tools · toggles | local store flip, instant | unchanged | OK |

New optimistic states use the store's existing machinery (`updateMessage`
status/tree patches, `siblingIndex`, `setRunningTaskFor`, `appendMessage`
placeholder). No parallel state.
