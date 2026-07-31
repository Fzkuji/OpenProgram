# Web state layer: current shape and the multi-page container plan

This page explains, from zero, where the web frontend keeps its state today,
which parts of it are already per-session and which are still a single global
value, why the recent split-view work kept producing bugs, and what the state
layer should become so that "two sessions on screen at once" stops being a
special case.

No frontend expertise is assumed. The three ideas you need are introduced
first, and everything after that is an inventory of real code with
`file:line` references.

---

## 1. Three ideas, in plain terms

**A store is a shared box of variables.** The web UI uses
[zustand](https://github.com/pmndrs/zustand), a small library where you create
one object holding both data (`currentSessionId`, `composerInput`) and the
functions that change it (`setCurrentConv`, `setComposerInput`). Any component
anywhere in the page can read any field from that box without it being passed
down through props. The main box is
`web/lib/session-store/index.ts:411`.

**A component subscribes to a slice.** When a component calls
`useSessionStore((s) => s.composerInput)`, React re-renders that component
whenever `composerInput` changes, and only then. The selector function is the
subscription.

**A component can be rendered more than once.** React components are
templates. `<Composer />` appears once in the normal layout and twice in a
split view. Both copies run the same code, so both copies read the same field
out of the same box. If that field is a single global value rather than a map
keyed by session, the two copies fight over it. That single sentence is the
root cause of most of section 5.

---

## 2. The main store: field-by-field inventory

`web/lib/session-store/index.ts` (906 lines) declares its shape in the
`ConvState` interface at `web/lib/session-store/index.ts:46` and its initial
values at `web/lib/session-store/index.ts:411`. Every field below is
classified into one of three groups.

### Group A — already isolated per session

These fields are `Record<sessionId, T>` maps. Two sessions on screen cannot
collide, because each reads its own key. This is the shape the whole store
should converge on.

| Field | Declared at | What it holds |
| --- | --- | --- |
| `conversations` | `web/lib/session-store/index.ts:68` | sidebar summary per session (keyed, but see Group C — it is a *list*, not per-session view state) |
| `messagesById` | `web/lib/session-store/index.ts:70` | every loaded message, keyed by message id |
| `messageOrder` | `web/lib/session-store/index.ts:72` | ordered message-id list per session |
| `pendingProjectsByChat` | `web/lib/session-store/index.ts:81` | project chosen for an unsent chat, keyed by provisional chat key |
| `runningTasks` | `web/lib/session-store/index.ts:90` | per-session running task; drives each composer's send/stop button |
| `trees` | `web/lib/session-store/index.ts:96` | latest live context tree per session |
| `tokens` | `web/lib/session-store/index.ts:103` | token usage per session |
| `contextWindow` | `web/lib/session-store/index.ts:112` | context-window size per session |
| `heads` | `web/lib/session-store/index.ts:115` | active DAG head (selected branch tip) per session |
| `additionalWorkingDirsBySession` | `web/lib/session-store/index.ts:146` | extra working directories per session |
| `composerDrafts` | `web/lib/session-store/index.ts:182` | unsent composer text per session, persisted to localStorage |
| `composerSettingsBySession` | `web/lib/session-store/index.ts:194` | tool toggles / thinking effort per session, persisted |
| `contextPanelFor` | `web/lib/session-store/index.ts:211` | *which* session has the `/context` popover open — a single field used as a per-session flag; see section 5 |

`pendingDecisions` (`web/lib/session-store/index.ts:244`) is a hybrid worth
calling out: it is a flat FIFO array, but each entry carries its own
`sessionId` (`web/lib/session-store/types.ts:60`), and the composer filters
the queue down to its own session at
`web/components/chat/composer/index.tsx:352`. Functionally it is already
session-scoped; structurally it is a list that every consumer must filter
correctly.

### Group B — global singletons whose meaning belongs to one session

This is the problem set. Each of these is a single value, but the thing it
describes is a property of one particular session or one particular pane. With
one session visible this is invisible; with two, the second pane either
overwrites the first or is forced to read the first's value.

| Field | Declared at | Why it should be scoped |
| --- | --- | --- |
| `currentSessionId` | `web/lib/session-store/index.ts:74` | "the" active session. With two panes there are two, and one is merely the *focused* one. |
| `activeChatKey` | `web/lib/session-store/index.ts:77` | same, for unsent drafts using a provisional `local_*` id |
| `runningTask` | `web/lib/session-store/index.ts:86` | explicitly documented as deprecated in favour of `runningTasks[sid]`; kept alive only so legacy `setRunning(false)` callers keep working. Mirrored on write at `web/lib/session-store/index.ts:678`. |
| `composerInput` | `web/lib/session-store/index.ts:178` | the *live* draft of the focused session; a mirror of `composerDrafts[focused]`. Written on every keystroke at `web/lib/session-store/index.ts:703`. |
| `composerSettings` | `web/lib/session-store/index.ts:193` | the *live* settings of the focused session; a mirror of `composerSettingsBySession[focused]`. Mirror logic at `web/lib/session-store/index.ts:727`. |
| `composerFocusTick` | `web/lib/session-store/index.ts:206` | a counter bumped to ask "the" composer to focus its textarea; with two composers it is ambiguous which one obeys |
| `fnFormFunction` | `web/lib/session-store/index.ts:217` | which function's parameter form has replaced the textarea. Belongs to one composer, not the app. |
| `fnFormPrefill` | `web/lib/session-store/index.ts:226` | prefilled arguments for that form |
| `fnFormForkOf` | `web/lib/session-store/index.ts:227` | fork anchor node for a re-run |
| `fnFormClosing` | `web/lib/session-store/index.ts:235` | close-animation flag for that form |
| `welcomeVisible` | `web/lib/session-store/index.ts:165` | whether the chat area shows the welcome screen — a per-pane condition |
| `transcriptLoadingId` | `web/lib/session-store/index.ts:172` | holds *one* in-flight session id; two panes can be loading at once |
| `branchInfo` | `web/lib/session-store/index.ts:62` | branch chip for "the current conversation" |
| `statusBadge` | `web/lib/session-store/index.ts:65` | topbar status label; derived from one session's run state |
| `paused` | `web/lib/session-store/index.ts:92` | pause flag, per running session in principle |
| `providerInfo` | `web/lib/session-store/index.ts:94` | provider/model shown in the header for the current session |
| `detailNode` | `web/lib/session-store/index.ts:261` | selected DAG node shown in the right rail |
| `nodeSelected` | `web/lib/session-store/index.ts:271` | "a DAG node is selected" gate |

`detailNode` and `nodeSelected` are listed here because they *describe* a
session's DAG, but they are deliberately a non-goal for the container work —
see section 8.

### Group C — genuinely global

These belong to the application, not to any session, and should stay single
values.

| Field | Declared at | What it is |
| --- | --- | --- |
| `wsStatus` | `web/lib/session-store/index.ts:48` | WebSocket connection state |
| `agentSettings` | `web/lib/session-store/index.ts:51` | Chat/Exec model badges, mirrored from `window._agentSettings` |
| `conversations` | `web/lib/session-store/index.ts:68` | the session *list* for the sidebar (a catalogue of all sessions, not one session's view state) |
| `rightDock` | `web/lib/session-store/index.ts:256` | right sidebar open/collapsed and which view, persisted to localStorage |

---

## 3. The other stores

Beyond the main session store, `web/lib/state/` holds several smaller stores.
None of them is on the critical path for session scoping, but knowing what
lives where prevents duplicating state later.

- **`web/lib/state/center-tabs-store.ts`** (1020 lines) — the tab strip and
  pane layout: `tabs`, `activeId`, `groups`, `splitWebTabId`, `splitRatio`
  (`web/lib/state/center-tabs-store.ts:129`). This is *view* state and is
  correctly global: it describes the window, not a session. It is also the
  store that knows a split exists at all, so it is where the scope tree gets
  its session ids from.
- **`web/lib/state/center-tab-groups.ts`** — pure functions over the tab
  layout (grouping, reordering, split panes). No state of its own.
- **`web/lib/state/chat-scroll.ts`** — scroll position helpers keyed by chat
  key, persisted through a storage interface
  (`web/lib/state/chat-scroll.ts:37`). Already per-session by construction,
  just not inside the store.
- **`web/lib/state/functions-store.ts`**, **`skills-store.ts`**,
  **`plugins-store.ts`** — page-level catalogues and their filter/sort/search
  UI state. Genuinely global; these are settings pages, not sessions.
- **`web/lib/state/files-shared.ts`** — project list, file reads, and file
  drafts keyed by path (`web/lib/state/files-shared.ts:144`). Per-file, not
  per-session.

---

## 4. The legacy `window.*` layer

There is a second, older state layer that predates the React store: plain
mutable properties on the browser's `window` object, written and read by the
modules under `web/lib/runtime-bridge/`. Their types are declared inline, for
example at `web/lib/runtime-bridge/chat-handlers.ts:34` and
`web/lib/runtime-bridge/conversations.ts:75`:

- `W.currentSessionId` — the legacy notion of the active session.
- `W.conversations` — a heavy per-session map holding full message arrays,
  distinct from the store's lightweight `conversations` summary map.
- `W.isRunning` — a single global "something is running" flag.
- `W.__sessionStore` — an escape hatch letting legacy code reach into the
  React store, used at `web/lib/runtime-bridge/chat-handlers.ts:895`.

### How the two layers relate

They are a **dual-track system kept in sync by hand**. WebSocket frames arrive
in the runtime bridge, which updates the `window.*` values *and* writes through
to the React store. `web/lib/runtime-bridge/conv-store-mirror.ts:4` documents
this explicitly: the sidebar reads `store.conversations`, so every mutation of
`window.conversations` must also call through the mirror to keep the store
authoritative.

Most incoming frames are gated on the legacy global rather than the store. The
pattern `data.session_id === W.currentSessionId` appears throughout
`web/lib/runtime-bridge/chat-handlers.ts` (lines 226, 249, 251, 271, 451, 524,
588, 747) and `web/lib/runtime-bridge/conversations.ts` (lines 332, 387, 527,
530, 536). Each of those is a place where an event for a *non-focused* session
is dropped or misrouted.

**What has already routed around it.** The split-view pane does not go through
the legacy shell at all. `web/components/chat/peer-session-pane.tsx:1` explains
why: the legacy `#chatView` shell is a singleton keyed on hardcoded DOM ids
(`#chatArea`, `#chatMessages`) read by roughly ten runtime-bridge modules, so
it cannot be mounted twice. AppShell hides it entirely while split
(`web/components/app-shell.tsx:552`) and each pane renders pure React off the
store instead. The pane even sends its own `load_session` request
(`web/components/chat/peer-session-pane.tsx:66`) because nothing in the legacy
path loads a session that is not focused.

**What still uses it.** Message rendering for the non-split path, session
switching, branch badge refresh, and most WebSocket frame routing. The legacy
layer is not dead; it is the default path, and the split view is the exception
that bypasses it.

---

## 5. Why the split-view work kept producing bugs

The pattern is always the same:

> The component is rendered twice, but the state it reads exists only once.

Two copies of `<Composer />` mount. Both run
`useSessionStore((s) => s.composerInput)`. There is one `composerInput`. So
either both panes show the same text, or the last one to write wins, or a
keystroke in the right pane appears in the left. Nothing in the type system
flags this, because reading a global field is exactly as legal in a split pane
as in a single one. The bug only manifests at runtime, only in split view, and
only after a specific interaction sequence — which is why they surface one at a
time rather than all at once.

### Worked example: `contextPanelFor`

The `/context` badge sits inside the composer and toggles a popover breaking
down the session's context usage.

Had this been a plain boolean `contextPanelOpen`, the split view would render
two badges, both subscribed to that one boolean. Clicking either badge would
open **both** popovers simultaneously, and closing one would close both.

The fix, visible at `web/lib/session-store/index.ts:209`, was to stop storing
"is it open" and start storing **which session it is open for**:

```ts
/** /context floating popover: holds "which session's panel is open"
 *  (null = closed). In a split view the two composers each render a
 *  badge, so keying by session is what stops both popping at once. */
contextPanelFor: string | null;
```

The consumer then compares against its own session id
(`web/components/chat/context-badge.tsx:44`):

```ts
const panelOpen = useSessionStore((s) => s.contextPanelFor != null && s.contextPanelFor === sid);
```

This is the whole design pattern in miniature. The state did not become
per-pane storage; it became **identified by session**, so each rendered copy
can ask "is this mine?" and only one answers yes.

The same shape already exists for `composerDrafts` and
`composerSettingsBySession`. Every remaining Group B field is a place where
this transformation has not happened yet.

### The mirror problem

`composerInput` and `composerSettings` are worse than plain globals: they are
**mirrors**. The real data lives in the keyed maps, and these two fields hold a
duplicate copy for whichever session is focused. Every write must update both
(`web/lib/session-store/index.ts:703` for input,
`web/lib/session-store/index.ts:727` for settings), and every session switch
must swap the mirror over (`switchChat` at
`web/lib/session-store/index.ts:367`).

The mirror also leaks into components. `web/components/chat/composer/index.tsx:166`
must branch on whether it is bound:

```ts
const input = useSessionStore((s) =>
  bound === null ? s.composerInput : (s.composerDrafts[bound] ?? ""),
);
```

The same three-line ternary is repeated for `fast`
(`web/components/chat/composer/index.tsx:460`) and `unattended`
(`web/components/chat/composer/index.tsx:480`), and again inside
`web/components/chat/composer/composer-session.tsx:39`. Each repetition is an
opportunity to get the fallback wrong. The mirror is also what
`web/lib/desktop-bridge.ts:677` and `web/lib/tab-transfer-journal.ts:249` have
to reconstruct when moving a session between windows.

---

## 6. The design: session scope as a container

### Core concept

A **session scope** is a declaration, made by a React component, that says
"everything below me in the component tree belongs to session X". Every
session-aware hook reads that declaration and automatically operates on that
session's slice. Components stop naming sessions and stop reading global active
slices; they just read "my session's draft", and the provider decides which
one that is.

The mechanism already exists in one corner of the codebase.
`web/components/chat/composer/composer-session.tsx:22` creates a React context
holding a chat key, and `web/components/chat/composer/composer-session.tsx:35`
exposes a hook that resolves it:

```ts
export function useBoundComposerSettings() {
  const bound = useComposerSessionKey();
  return useSessionStore((s) =>
    bound === null
      ? s.composerSettings
      : (s.composerSettingsBySession[bound] ?? s.composerSettings),
  );
}
```

The split pane wraps its composer in it at
`web/components/chat/peer-session-pane.tsx:234`, and three control hooks
already consume it: `use-permission-mode.ts:48`, `use-tools-toggles.ts:29`, and
`use-thinking-effort.ts:73` (all under
`web/components/chat/composer/controls/`).

**The proposal is to promote this from a composer-local trick to the state
layer's primary access pattern.** Two changes make it general:

1. The provider moves up. Instead of wrapping only the composer, a
   `SessionScopeProvider` wraps each pane's entire subtree — message list,
   composer, context badge, welcome screen. The single-pane layout gets one
   too, wrapping the whole chat view with the focused session.
2. The `null` fallback goes away. Today `null` means "follow the focused
   session", which preserves old behaviour but leaves the global-read path
   legal. Once every subtree is wrapped, an unscoped read becomes a bug, and a
   lint rule can forbid components from touching `s.composerInput`,
   `s.currentSessionId`, or `s.fnFormFunction` directly.

### Three layers

| Layer | Contains | Access |
| --- | --- | --- |
| **Global state** | `wsStatus`, `agentSettings`, `conversations` (the list), `rightDock`, preferences | read directly from the store |
| **Session state container** | `sessions: Record<sid, SessionState>`, each holding `messages`, `draft`, `settings`, `running`, `panels`, `scroll` | read only through scope-aware hooks |
| **View state** | tab strip, pane layout, split ratio, which pane has focus | `center-tabs-store`; belongs to the window, not a session |

The target session slice, gathering today's scattered maps:

```ts
interface SessionState {
  messageIds: string[];
  draft: string;
  settings: ComposerSettings;
  running: RunningTask | null;
  head: string | null;
  tokens: TokenUsage | null;
  contextWindow: number | null;
  tree: TreeNode | null;
  additionalWorkingDirs: string[];
  panels: { contextOpen: boolean; fnForm: FnFormState | null };
  transcriptLoading: boolean;
  welcomeVisible: boolean;
}
```

Whether this is literally one nested map or stays as today's parallel
`Record<sid, T>` maps is an implementation detail with real performance
consequences — a nested object means any write to one session invalidates
subscribers of the whole `sessions` object unless selectors are written
carefully. **Keeping the parallel-map layout and changing only the access
pattern is the cheaper path**, and it is what the migration below assumes.
`messagesById` stays flat and global, since it is keyed by message id and
already benefits from being shared.

The distinction that matters is not the storage shape. It is that **no
component reads a session field without a scope**.

---

## 7. Migration path

### Stage 1 — eliminate the active-slice mirrors

Remove `composerInput` and `composerSettings` as stored fields. Every read goes
through the scope; the focused session's scope resolves to
`composerDrafts[focused]` and `composerSettingsBySession[focused]`.

Files involved:

- `web/lib/session-store/index.ts` — delete both fields, rewrite
  `setComposerInput` (`:703`), `setComposerInputFor` (`:712`),
  `setComposerSettings` (`:727`), and drop the mirror-swapping half of
  `switchChat` (`:367`).
- `web/components/chat/composer/composer-session.tsx` — the `null` branches at
  `:39` and `:51` collapse to a focused-session lookup.
- `web/components/chat/composer/index.tsx` — the ternaries at `:166`, `:460`,
  `:480` become plain scoped reads.
- `web/lib/tab-transfer-journal.ts:249` and `web/lib/desktop-bridge.ts:677`,
  `:707`, `:835` — these serialize the mirror across window moves and must be
  reworked to carry only the keyed maps plus a focused key.

Risks: the snapshot/restore path is the sharp edge. `snapshotSessionTransfer`
(`web/lib/session-store/index.ts:819`) and `applySessionTransfer`
(`web/lib/session-store/index.ts:840`) both carry `composerInput` and
`composerSettings` in the wire format, so the transfer journal's shape changes
and stale persisted blobs must degrade gracefully. Second risk: a lost draft on
session switch, since `switchChat` currently stores the outgoing session's live
text into the map — with no mirror there is nothing to store, but the ordering
between "write keystroke to map" and "change focused key" must be verified.

Scale: one store file, three composer files, two persistence files. Small
surface, high blast radius on drafts — this stage deserves a manual pass
through the type/refresh/switch/split sequence.

### Stage 2 — move the remaining Group B fields into the container

Field by field, in rough order of independence:

1. `fnFormFunction` / `fnFormPrefill` / `fnFormForkOf` / `fnFormClosing` →
   `sessions[sid].panels.fnForm`. Touches
   `web/components/chat/composer/modes/resolve-mode.ts:18`,
   `web/components/chat/composer/modes/fn-form/use-fn-form-state.ts`,
   `use-fn-form-wrapper.ts`, `web/lib/use-pending-run-function.ts`,
   `web/components/sidebar/favorites-list.tsx`,
   `web/components/sidebar/sidebar.tsx`,
   `web/components/chat/messages/runtime-block.tsx`, and
   `web/lib/runtime-bridge/functions-panel.ts`. Note that the sidebar and
   favourites list *open* a form without knowing which pane should host it —
   they need an explicit target session, which is a genuine behaviour decision,
   not a mechanical port.
2. `welcomeVisible`, `transcriptLoadingId` → per-session booleans. Consumers:
   `web/components/chat/welcome-screen.tsx`,
   `web/components/chat/messages/message-list.tsx`.
3. `runningTask` → delete; `runningTasks[sid]` is already authoritative and the
   mirror at `web/lib/session-store/index.ts:678` disappears with it. Requires
   auditing legacy `setRunning` callers.
4. `composerFocusTick` → per-session counter, so focusing one pane's textarea
   does not yank the other.
5. `branchInfo`, `statusBadge`, `paused`, `providerInfo` → per-session. These
   feed the topbar, which shows the *focused* session, so the topbar reads the
   focused scope and nothing else changes visually.
6. `currentSessionId` / `activeChatKey` → keep as *global focus pointers* in
   the view layer. They stop meaning "the session" and start meaning "the
   focused pane's session", which is what `center-tabs-store` already tracks
   via `activeId`. This is the last one to move and the one that most affects
   the runtime bridge.

Risks: `pendingDecisions` routing must be re-checked at each step — it is
filtered by `sessionId` at `web/components/chat/composer/index.tsx:352`, and a
mis-scoped composer would either swallow another session's question or show it
twice. The fn-form group is the largest single change because eight files
consume it.

Scale: roughly fifteen to twenty files, but decomposable into six independent
commits, each individually verifiable.

### Stage 3 — retire the legacy `window.*` layer

This cannot start before stage 2 finishes, because the bridge's routing gates
read `W.currentSessionId` and there is no correct replacement until the store
distinguishes "focused" from "the session".

Retirement conditions, all of which must hold:

1. Every `data.session_id === W.currentSessionId` gate in
   `web/lib/runtime-bridge/chat-handlers.ts` and `conversations.ts` is replaced
   by a store write keyed on `data.session_id`, so frames for background
   sessions land instead of being dropped.
2. `window.conversations` has no readers left that the store's keyed maps do
   not cover; `web/lib/runtime-bridge/conv-store-mirror.ts` then becomes
   unnecessary rather than load-bearing.
3. `W.isRunning` has no readers; `runningTasks` covers it.
4. The singleton `#chatView` DOM shell is gone, replaced everywhere by the
   React path that `PeerSessionPane` already proves works. This is the
   heaviest precondition — roughly ten runtime-bridge modules address it by
   hardcoded id.

Risks: this is the stage where a mistake silently drops WebSocket frames
rather than rendering wrong. Every gate removal needs a paired verification
that the frame still reaches its session.

Scale: the whole of `web/lib/runtime-bridge/` (thirteen modules plus the `dag`
subdirectory). Largest stage by far, and the only one where partial completion
leaves the codebase worse than either endpoint — so it should be sequenced
last and executed as a single sustained effort per module.

---

## 8. Non-goals

**The right sidebar and the DAG stay single-instance, following the focused
session.** `detailNode` and `nodeSelected`
(`web/lib/session-store/index.ts:261`, `:271`) remain global. Nothing renders
them twice: the right rail is one dock
(`web/components/right-sidebar/right-sidebar.tsx:407`, `:575`), and there is no
plan to give each pane its own DAG. They can keep reading the focused session
and be correct.

The same applies to `rightDock` itself, the topbar badges' *display* (they show
the focused session by definition), and the settings pages' stores.

**Storage shape is not being redesigned.** The migration changes how state is
*accessed*, not necessarily how it is laid out. Collapsing today's parallel
`Record<sid, T>` maps into one nested `sessions` object is optional, carries
subscription-granularity risk, and is not required for correctness.

**More than two panes is not in scope.** The scope mechanism happens to make
N panes work, but nothing here is designed or verified for N > 2.

---

## 9. Effort by stage

| Stage | Files touched | Main risk |
| --- | --- | --- |
| 1 — remove active-slice mirrors | ~6 (store, 3 composer, 2 persistence) | draft loss on switch; transfer-journal wire format changes |
| 2 — Group B into the container | ~15–20, splittable into 6 commits | fn-form open-target semantics; `pendingDecisions` routing |
| 3 — retire `window.*` | ~13 runtime-bridge modules + DOM shell | silently dropped WebSocket frames; partial completion is worse than either endpoint |

Stages 1 and 2 are independently shippable and each leaves the codebase in a
better state than it found it. Stage 3 is not, and should be treated as a
single project rather than incremental cleanup.

---

## Related

- [`composer-interaction-modes.md`](composer-interaction-modes.md) — how the
  composer arbitrates between idle, fn-form, question, and approval modes
- [`invariants.md`](invariants.md) — cross-module UI invariants
- [`interaction-feedback.md`](interaction-feedback.md) — the optimistic-state
  rule that shapes how these writes are ordered
