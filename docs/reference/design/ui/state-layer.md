# Web state layer: per-session stores

This page explains, from zero, where the web frontend keeps its state, why the
split-view work kept producing bugs, and the decision taken to stop it: **one
Zustand store instance per session, with genuinely shared data left in the
global store**. Section 6 records that decision and section 7 tracks the
migration against it.

The inventory in sections 2 to 5 describes the state layer as it was before
that work began. It is kept because it is what makes the decision legible —
each field it lists as a problem is either fixed in stage 1 or still queued in
stage 2.

No frontend expertise is assumed. The three ideas you need are introduced
first, and everything after that is an inventory of real code with
`file:line` references.

---

## 1. Three ideas, in plain terms

**A store is a shared box of variables.** The web UI uses
[zustand](https://github.com/pmndrs/zustand), a small library where you create
one object holding both data (`currentSessionId`, `composerDrafts`) and the
functions that change it (`setCurrentConv`, `setComposerInput`). Any component
anywhere in the page can read any field from that box without it being passed
down through props. The main box is
`web/lib/session-store/index.ts:411`.

**A component subscribes to a slice.** When a component calls
`useSessionStore((s) => s.conversations)`, React re-renders that component
whenever `conversations` changes, and only then. The selector function is the
subscription.

**A store can be created more than once.** Nothing about zustand requires a
single global box. `createStore` can be called per session, and a React
context can hand each subtree the instance that belongs to it. That is the
shape section 6 settles on.

**A component can be rendered more than once.** React components are
templates. `<Composer />` appears once in the normal layout and twice in a
split view. Both copies run the same code, so both copies read the same field
out of the same box. If that field is a single global value rather than a map
keyed by session, the two copies fight over it. That single sentence is the
root cause of most of section 5.

---

## 2. The main store: field-by-field inventory

`web/lib/session-store/index.ts` declares its shape in the `ConvState`
interface and its initial values in the `create<ConvState>` call. Every field
below is classified into one of three groups. Line numbers are as of the
pre-migration snapshot; fields marked **removed in stage 1** no longer exist.

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
| `contextPanelFor` | `web/lib/session-store/index.ts:211` | *which* session has the `/context` popover open — a single field used as a per-session flag; see section 5. **Removed in stage 1**: it is now `contextPanelOpen` on each session's own store. |

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
| `runningTask` | `web/lib/session-store/index.ts:86` | deprecated in favour of `runningTasks[sid]`; kept alive only so legacy `setRunning(false)` callers kept working. **Removed in stage 1.** |
| `composerInput` | `web/lib/session-store/index.ts:178` | the *live* draft of the focused session; a mirror of `composerDrafts[focused]`. **Removed in stage 1.** |
| `composerSettings` | `web/lib/session-store/index.ts:193` | the *live* settings of the focused session; a mirror of `composerSettingsBySession[focused]`. **Removed in stage 1.** |
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

That is the keyed-global pattern in miniature, and it works: each rendered
copy asks "is this mine?" and only one answers yes.

It is also the pattern section 6 declines to generalise. Every consumer still
has to compare against the right id, and a consumer that compares against the
wrong one — or against the focused session — is still type-correct. Under the
per-session store this field is just `contextPanelOpen`, a plain boolean in
the instance that belongs to the badge's own session, with no comparison to
get wrong.

### The mirror problem

`composerInput` and `composerSettings` were worse than plain globals: they
were **mirrors**. The real data lived in the keyed maps, and these two fields
held a duplicate copy for whichever session was focused. Every write had to
update both, and every session switch had to swap the mirror over
(`switchChat`).

The mirror also leaked into components.
`web/components/chat/composer/index.tsx:166` had to branch on whether it was
bound:

```ts
const input = useSessionStore((s) =>
  bound === null ? s.composerInput : (s.composerDrafts[bound] ?? ""),
);
```

The same three-line ternary was repeated for `fast` and `unattended`, and
again inside `composer-session.tsx`. Each repetition was an opportunity to get
the fallback wrong, and the `bound === null` arm meant the wrong answer was
"silently use the focused session" rather than an error. The mirror was also
what `desktop-bridge.ts` and `tab-transfer-journal.ts` had to reconstruct when
moving a session between windows.

Stage 1 deleted all of it. The scope owns the live value, so there is nothing
to mirror, no fallback arm, and nothing for the transfer path to rebuild.

---

## 6. The design: one store instance per session

### The decision

**Each session gets its own Zustand store instance. Genuinely shared data
stays in the single global store.**

A component does not name a session and does not read a global active slice.
It reads "my draft", and the enclosing `SessionScopeProvider` decides whose.
Rendering the same component twice against two sessions is then the ordinary
case rather than the case that breaks: the two copies are subscribed to two
different stores and cannot collide, because there is nothing shared to
collide over.

The split is by *ownership*, not by storage shape:

| Layer | Contains | Access |
| --- | --- | --- |
| **Global store** | `wsStatus`, `agentSettings`, `conversations` (the list), `messagesById`, `rightDock`, and the durable per-session maps (`composerDrafts`, `composerSettingsBySession`, `runningTasks`) | `useSessionStore` |
| **Per-session store instance** | this session's `draft`, `settings`, `running`, `contextPanelOpen` | `useSessionScope`, inside a `SessionScopeProvider` |
| **View state** | tab strip, pane layout, split ratio, which pane has focus | `center-tabs-store`; belongs to the window, not a session |

### The pieces

`web/lib/session-store/session-scope-registry.ts` holds the store factory and
a module-level `Map<sid, store>`. Instances are cached and **survive pane
unmount** — tabbing away and back keeps the draft you were typing. They are
dropped only when the session itself is deleted (`dropSessionStore`, called
from `removeConversation` and `dropChatDraft`).

`web/lib/session-store/session-scope.tsx` is the React layer:
`SessionScopeProvider sid=…` and `useSessionScope(selector)`.

**There is no unbound path.** `useSessionScope` throws when no provider
encloses the component rather than falling back to the focused session. A
silent fallback is precisely the bug this layer removes, and it would surface
only in a split view after a specific interaction sequence; throwing makes a
missing wrap obvious on the first render. Two providers exist and between them
cover every composer: `FocusedComposer` in
`web/components/app-shell.tsx` wraps the single-session composer with the
focused chat key, and `PeerSessionPane` wraps each split pane with its own.

### Why instances rather than a keyed global slice

Both shapes stop the collision. The instance-per-session shape was chosen
because it removes the *possibility* of writing the bug, not just the current
instances of it. With a keyed global slice every consumer still has to supply
the right key on every read and write, and supplying the wrong one — or
omitting it and getting the focused session — stays type-correct. With an
instance, the key is supplied once by the provider and the component has no
way to name another session accidentally.

It also keeps subscriptions naturally narrow: a keystroke in one session
notifies only that session's subscribers, with no selector care required.

### Durable state still belongs to the global store

Instances hold live state. Anything that must outlive the tab — localStorage
persistence, the tab-transfer wire format, the sidebar's cross-session views —
stays in the global keyed maps. The two are kept in step in both directions:

- **Scope → global.** A scope setter updates its own instance first (so the
  pane repaints on the same tick), then writes through to the global keyed
  setter. The hooks are installed by the global store at module init via
  `installScopeWriteThrough`, which is what keeps the import one-directional.
- **Global → scope.** `setComposerInputFor`, `setComposerSettings` and
  `setRunningTaskFor` call `pushToSessionStore`, so a WS frame, a legacy
  bridge, or a window-to-window tab transfer lands in the live instance too.

The instance seeds from the global maps on first render, which is what makes
remount-after-reload show the persisted draft rather than an empty box.

### Consequence: the mirrors are gone

Because the scope owns the live value, the global store no longer needs a
"live slice for the focused session" copy of it. `composerInput`,
`composerSettings` and `runningTask` were deleted outright, along with
`contextPanelFor` (a single field used as a per-session flag). The focused
session's draft is now simply `composerDrafts[activeChatKey]`, computed where
it is needed instead of stored twice.

### Considered and rejected: a global store with scope tags

The alternative was to keep one global store and make every session-scoped
field a `Record<sid, T>` map, with a React context supplying the key so
consumers read `map[scopeKey]` rather than a global. `contextPanelFor` is that
pattern in miniature: it stores *which* session has the panel open, and each
badge asks "is this mine?".

It works, and it is a smaller diff. It was rejected because it leaves the
failure mode alive: the key is still passed on every access, an omitted key
still silently means "the focused session", and nothing in the type system
distinguishes a correct read from a mis-scoped one. The parts of it that were
right — parallel `Record<sid, T>` maps rather than one nested `sessions`
object, `messagesById` staying flat and global — carry over unchanged, since
those maps are exactly what the instances persist through.

---

## 7. Migration path

### Stage 1 — stand up the scope, delete the mirrors — **done**

Built `session-scope-registry.ts` (store factory, instance map, write-through
hooks) and `session-scope.tsx` (`SessionScopeProvider`, `useSessionScope`).

Deleted from the global store: `composerInput`, `composerSettings`,
`runningTask`, `contextPanelFor`, plus the setters `setRunningTask` and
`setContextPanelFor`.

Migration points:

- `switchChat` no longer saves the outgoing session's text or loads the
  incoming one — both already live in the keyed maps. All that remains is
  promoting the `__new__` placeholder key when an unsent chat gets a real id.
- `web/components/chat/composer/index.tsx` — the four
  `bound === null ? global : keyed[bound]` ternaries (draft, `fast`,
  `unattended`, settings setter) collapsed to plain `useSessionScope` reads.
  `bound` survives only for what it actually distinguishes: `background: true`
  on send, and per-pane DOM id suffixes.
- `web/components/chat/composer/composer-session.tsx` — reduced to two
  one-line helpers over the scope; the context and the `null` fallback are
  gone. The three control hooks (`use-thinking-effort`, `use-tools-toggles`,
  `use-permission-mode`) needed no change, since they consume those helpers.
- `web/components/app-shell.tsx` — the portalled `<Composer />` became
  `<FocusedComposer />`, which wraps it in a provider keyed on
  `activeChatKey ?? currentSessionId ?? "__new__"`. This is what let the
  unbound branch be deleted rather than merely bypassed.
- `web/components/chat/peer-session-pane.tsx` — swapped
  `ComposerSessionProvider` for `SessionScopeProvider`, so the pane's whole
  composer subtree (draft, run state, settings, `/context`) is scoped, not
  just the control hooks.
- `web/components/chat/context-badge.tsx` — `contextPanelFor === sid` became
  `useSessionScope(s => s.contextPanelOpen)`.
- Wire format: `SessionTransferSnapshot` dropped `composerInput` /
  `composerSettings`, and `ChatTransferState` dropped `activeComposerInput` /
  `activeComposerSettings`. All four were duplicates of the keyed entry for
  `activeChatKey`, which the receiving window can look up itself.
  `desktop-bridge.ts` and `tab-transfer-journal.ts` follow.
- Legacy bridges: `runtime-bridge/ui.ts` and `chat-handlers.ts` already
  preferred `setRunningTaskFor`; their dead `setRunningTask` declarations were
  removed. That keyed setter now also pushes into the live instance.

Verification: `npx tsc --noEmit` clean, `npx next build` passes, all ten
`npm run check` suites pass. `check-provisional-send` traded a source-shape
assertion about the old mirror guard for a behavioural one (patching a
background session leaves the focused one alone, and does not let the
background session inherit its values); `check-multi-draft` and
`check-web-split` now read the focused draft as
`composerDrafts[activeChatKey]`. The five harnesses' module resolvers also
gained an extensionless-relative-import clause and a `fileURLToPath` fix for
repo paths containing spaces.

### Stage 2 — move the remaining Group B fields into the scope

Each remaining field becomes a property of `SessionScopeState` and its
consumers switch to `useSessionScope`. In rough order of independence:

1. `fnFormFunction` / `fnFormPrefill` / `fnFormForkOf` / `fnFormClosing` →
   one `fnForm` property on the scope. Touches
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
3. `composerFocusTick` → per-session counter, so focusing one pane's textarea
   does not yank the other.
4. `branchInfo`, `statusBadge`, `paused`, `providerInfo` → per-session. These
   feed the topbar, which shows the *focused* session, so the topbar reads the
   focused scope and nothing else changes visually.
5. `currentSessionId` / `activeChatKey` → keep as *global focus pointers* in
   the view layer. They stop meaning "the session" and start meaning "the
   focused pane's session", which is what `center-tabs-store` already tracks
   via `activeId`. This is the last one to move and the one that most affects
   the runtime bridge.

Risks: `pendingDecisions` routing must be re-checked at each step — it is
filtered by `sessionId` at `web/components/chat/composer/index.tsx:352`, and a
mis-scoped composer would either swallow another session's question or show it
twice. The fn-form group is the largest single change because eight files
consume it.

Scale: roughly fifteen to twenty files, decomposable into five independent
commits, each individually verifiable. The scope infrastructure already exists,
so each field is a move rather than a new mechanism.

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

**The durable maps keep their shape.** The parallel `Record<sid, T>` maps in
the global store stay as they are; they are what the per-session instances
seed from and persist through. Collapsing them into one nested `sessions`
object is not planned and is not required for correctness.

**More than two panes is not in scope.** The scope mechanism happens to make
N panes work, but nothing here is designed or verified for N > 2.

---

## 9. Effort by stage

| Stage | Files touched | Main risk | Status |
| --- | --- | --- | --- |
| 1 — scope infrastructure, delete the mirrors | 2 new, ~10 edited (store, composer, app-shell, peer pane, badge, 2 persistence, 2 bridges), 5 check harnesses | draft loss on switch; transfer-journal wire format changes | done |
| 2 — remaining Group B fields into the scope | ~15–20, splittable into 5 commits | fn-form open-target semantics; `pendingDecisions` routing | queued |
| 3 — retire `window.*` | ~13 runtime-bridge modules + DOM shell | silently dropped WebSocket frames; partial completion is worse than either endpoint | queued |

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
