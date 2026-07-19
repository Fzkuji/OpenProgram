# Desktop Multi-window Tab Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task by task. Use TDD and commit after each task.

**Goal:** Move a normal tab, one compound segment, or a whole compound group into another/new OpenProgram window and back while preserving renderer state and the same live `WebContentsView` objects.

**Architecture:** Each `BrowserWindow` owns a `WindowContext` with a set of visible native views. The compound plan's single drag coordinator synchronously holds one prepared token and resolves before/merge/after geometry for same-window and cross-window drops. Main owns a pre-commit transfer transaction: destination staging and native reparenting are reversible until the source renderer confirms successful reversible removal. That confirmation is the commit boundary; main finalizes immediately and never rolls back afterward. Hidden detach windows pull their pending token after renderer hydration with `claimPending(windowId)`.

**Tech stack:** Electron 37 `BrowserWindow` / `WebContentsView` / IPC, React 18, Zustand 5, TypeScript, native HTML Drag and Drop, Node `assert`/`vm`, CDP. No new drag dependency.

## Non-negotiable invariants

- Test only against the development UI on port `18200` until final promotion. Do not modify stable `18100`.
- Reuse `CenterTabGroup` from `web/lib/state/center-tab-groups.ts` and the unified `CenterTabsPersistedPayload` from `center-tabs-store.ts`. Do not redefine either and do not create a group-only storage key.
- Renderer state is stored in `centerTabs:<windowId>`. Only the main window migrates the old unkeyed payload once. Split/group state is in that same payload.
- `WindowContext.visibleViewIds` is a set. Two visible web panes may simultaneously have non-zero bounds and `visible=true`.
- Every native-view IPC derives its context from `event.sender`; a source window cannot hide, resize, navigate, or destroy a view after ownership moves.
- `visibleWebTab()` is group-aware: prefer the focused visible web member, then another visible web pane, then a full-width active web tab. Hidden group members are not model-control targets.
- Focused-window routing is deterministic. Menu and backend commands go to the focused live window, or the most recently focused live window only when none is focused.
- One shared `tab-drag-coordinator.ts` owns payload, prepared ref, cancellation, and geometry. Do not add a second same-window/cross-window drag ref or placement resolver.
- Pointer/mouse down prepares a main-process transfer token synchronously. `dragstart` only reads the prepared token and writes `DataTransfer` synchronously; it contains no `await`.
- Pointer release or click without `dragstart` cancels that prepared token immediately. Close-button pointer events never prepare a token.
- Drop placement always uses target geometry: first 25% `before`, middle 50% `merge`, final 25% `after`.
- A whole compound group transfers atomically with all members. A segment transfer carries its source group id, member index, `visibleIds`, and `focusedId`.
- Destination duplicate ids and a merge that would exceed three members use explicit `reject(token, reason, duplicateId?)` before native reparenting or draft import. A duplicate activates the existing destination tab; the source remains unchanged.
- If a transferred web id already has a native record, transfer reparents that exact `WebContentsView` and never calls `loadURL` or closes `webContents`. A web tab without a native record is still valid metadata; the destination's first visible `WebTabPane` creates it through the existing `ensure` path.
- Ordinary `closeTab()` / `finishClose()` are never used for transfer. Transfer must not create close tombstones, delete draft data irreversibly, or add an NTP fallback.
- Destination staging completes before source removal is requested.
- Source removal is reversible and returns a recovery snapshot for tabs/groups/order/active/split, drafts, session state, and renderer bridge bookkeeping.
- Main commits immediately when the source renderer reports successful removal. After that line, the active token is deleted, the timeout is cleared, and rollback is impossible. A read-only terminal receipt remains so reloaded renderers can resolve their journals; it cannot accept/cancel/rollback the transfer.
- Every committed/rolled-back decision is durably and atomically stored at `app.getPath("userData")/tab-transfers.json` before either renderer persists its journal result. Main restart reloads the same decision; it is deleted only after both source and destination roles durably acknowledge finalization.
- If source removal fails, races a timeout, or main rejects its acknowledgement, the source restores its recovery snapshot and main explicitly undoes destination tabs/drafts/bookkeeping before restoring native ownership.
- No timeout path may leave the source deleted while only reparenting the view. A late source acknowledgement must receive `false` and restore locally.
- Provisional center-tab/session changes use `{ persist: false }`. Before any transient mutation, write a complete recovery journal to `openprogram.tabTransferJournal:<windowId>`; `centerTabs:<windowId>` and existing composer persistence remain at the last committed state until main commits.
- `acceptedTransfers`, journal entries, and every imported draft snapshot are deleted on both final commit and rollback. Renderer startup resolves the journal against main's token status before normal native-view reconciliation.
- Destination rollback order is fixed: forget destination bridge/native-view bookkeeping, undo transient session/store state, acknowledge destination undo, then let main reparent/restore the records. Main rejects ordinary `webtab:destroy` for every record locked by a pre-commit transaction.
- A detached window remains hidden until commit. It claims pending work after renderer handlers and store hydration; no one-shot `did-finish-load` send is used.
- Session transfer uses the real `activeChatKey` and changes the route plus `useSessionStore.currentSessionId` through the compound plan's shared active-session effect. Draft payload/recovery includes `composerDrafts`, `composerSettingsBySession`, `pendingProjectsByChat`, live `composerInput`/`composerSettings` when that chat is active, and the keyed draft channel choice.
- Normal committed draft state is persisted per window at `openprogram.sessionDraftState:<windowId>`; the transfer journal is recovery metadata, not the sole post-commit storage for pending projects or channel choices.

## Files

- Modify `desktop/main.js`.
- Create `desktop/tab-transfer-store.js`.
- Modify `desktop/package.json`.
- Modify `desktop/preload.js`.
- Modify `desktop/scripts/check-webtab-navigation.js`.
- Modify `web/lib/desktop-bridge.ts`.
- Create `web/lib/tab-transfer-journal.ts`.
- Modify `web/lib/tab-drag-coordinator.ts`.
- Modify `web/lib/state/center-tabs-store.ts`.
- Modify `web/lib/session-store/index.ts`.
- Create `web/lib/session-draft-persistence.ts`.
- Modify `web/lib/runtime-bridge/draft-channel-choice.ts`.
- Modify `web/lib/state/files-shared.ts`.
- Modify `web/components/app-shell.tsx`.
- Modify `web/components/center-tabs/center-tab-strip.tsx`.
- Modify `web/components/center-tabs/center-tabs.module.css`.
- Modify `web/scripts/check-web-split.mjs`.
- Modify `web/scripts/check-center-tabs.mjs`.
- Create `web/public/desktop-transfer-acceptance.html`.

### Task 1: Scope native views and visible sets by window

**Files:**

- Modify `desktop/main.js`
- Create `desktop/tab-transfer-store.js`
- Modify `desktop/package.json`
- Modify `desktop/scripts/check-webtab-navigation.js`

**Durable terminal-decision store:**

```js
// app.getPath("userData")/tab-transfers.json
{
  version: 1,
  decisions: {
    [token]: {
      token,
      status: "committed" | "rolled-back",
      sourceId,
      destinationId,
      sourceEmpty,
      requiredRoles: [
        { role: "source", windowId: "source-window-id" },
        { role: "destination", windowId: "destination-window-id" },
      ],
      finalizedRoles: [],
      decidedAt,
    },
  },
}

loadTransferDecisions(filePath)
saveTransferDecisionsAtomic(filePath, value)
putTransferDecision(filePath, decision)
ackTransferDecision(filePath, token, role)
```

`saveTransferDecisionsAtomic` writes mode `0600` to a sibling temporary file, `fsync`s it, renames it over `tab-transfers.json`, and `fsync`s the user-data directory. A failed write leaves the previous valid file intact. Add `tab-transfer-store.js` to `desktop/package.json` `build.files`.

`requiredRoles` contains only participants that successfully wrote a renderer journal, recorded by `tab-transfer:journal-opened` before transient mutation. `putTransferDecision` returns only after the new terminal status is durable. `ackTransferDecision` rejects an unknown/non-required role and otherwise returns `{ decision, complete }` after atomically persisting the updated `finalizedRoles`; an already-recorded role is idempotent. When `complete` is true, that same atomic save has removed the token from `decisions`. A write/fsync/rename failure throws and leaves the previously durable decision unchanged.

Renderer local storage is shared by the OpenProgram Electron partition, while transfer keys are scoped by `windowId`. If a required participant is destroyed before acknowledging, main asks a surviving renderer to finalize that orphaned `{ role, windowId }`: it reads `openprogram.tabTransferJournal:<windowId>`, applies the durable decision's before/after payload to that window's keyed normal storage, deletes the orphan journal, and then acknowledges the original role. If no renderer survives, the durable decision remains; the first renderer after restart receives the orphan work through `pending-terminal` and performs the same idempotent cleanup. Main never acknowledges a destroyed journal owner without this durable keyed-storage cleanup.

**Main-process model:**

```js
function makeWindowContext(id, win) {
  return {
    id,
    win,
    views: new Map(),
    visibleViewIds: new Set(),
    pendingTransferToken: null,
  };
}

const windows = new Map();
const contextsByBrowserWindowId = new Map();
let lastFocusedWindowId = null;
```

Each view map value is `{ id, view, ownerId, navigation }`. `ownerId` changes with transfer; state events resolve the current owner before sending.

**Visibility API:**

```js
function syncVisibleViews(ctx, items) {}
function showView(ctx, id, bounds) {}
function hideView(ctx, id) {}
```

`items` is `Array<{ id, bounds }>` for all web panes in one renderer layout. `syncVisibleViews` validates ownership for every id, applies bounds and `setVisible(true)` to all desired records, hides records not in the desired set, then replaces `ctx.visibleViewIds`. `showView`/`hideView` update a copied desired collection and delegate to `syncVisibleViews`; neither assumes a singleton.

- [ ] Extend `check-webtab-navigation.js` with executable fake windows/content views and two view records. Call `syncVisibleViews(ctx, [{id:"a",...},{id:"b",...}])`; assert both are visible, both ids are in `visibleViewIds`, and both bounds are non-zero. Remove `a`; assert only `a` hides. This must be a behavior test, not only a regex.
- [ ] Add behavior tests for sender ownership: invoke extracted handlers with two fake `event.sender` values, then try to hide/destroy window A's view from B. Assert the view remains visible/open and B's set is unchanged.
- [ ] Add behavior tests for focused routing: change the fake `BrowserWindow.getFocusedWindow()` result and then remove focus; assert `focusedContext()` returns A, then B, then the most-recent live B. Destroy B; assert fallback is A or null, never the destroyed context.
- [ ] Run `npm --prefix desktop run check:webtabs`; expect failure on singleton globals/visibility.
- [ ] Replace `mainWindow`, global `views`, global navigation maps, and `visibleViewId` with contexts. Refactor ensure/navigate/activate/bounds/show/hide/destroy/reload/back/forward handlers to call `contextForSender(event)` and reject unowned ids.
- [ ] Add `webtab:sync-visible` IPC. Keep old show/hide channels as collection-aware compatibility wrappers until renderer migration finishes.
- [ ] Menu dispatch calls `focusedContext()` at invocation time.
- [ ] Window cleanup closes only records still owned by that context and removes both registries.
- [ ] Run `npm --prefix desktop run check:webtabs`; all behavior cases pass.
- [ ] Commit: `refactor(desktop): scope native views by window`.

### Task 2: Expose window identity and synchronize both native panes

**Files:**

- Modify `desktop/preload.js`
- Modify `web/lib/desktop-bridge.ts`
- Modify `web/components/app-shell.tsx`
- Modify `web/scripts/check-web-split.mjs`
- Modify `desktop/scripts/check-webtab-navigation.js`

**Bridge additions:**

```ts
interface DesktopVisibleWebView {
  id: string;
  bounds: { x: number; y: number; width: number; height: number };
}

interface DesktopWebTabApi {
  syncVisible(items: DesktopVisibleWebView[]): void;
  // existing ensure/navigate/activate/bounds/show/hide/destroy/back/forward
}

interface DesktopBridge {
  readonly isDesktop: true;
  readonly windowId: string;
  readonly webTab: DesktopWebTabApi;
}
```

- [ ] Add a renderer behavior harness to `check-web-split.mjs`: register two pane bounds in the bridge's local `Map`, flush the scheduler, and assert one `syncVisible` call contains both ids. Remove one and assert the next call contains only the survivor. Do not accept two sequential singleton `show` calls as coverage.
- [ ] Add group-aware selection checks: for visible web ids `[w:a,w:b]` and `focusedId=w:b`, `visibleWebTab()` returns B; when focus is a session, it returns the first visible web; a hidden web id is ignored.
- [ ] Run desktop and web checks; expect missing `windowId` / `syncVisible` failures.
- [ ] Parse `--openprogram-window-id=` once in preload. `createWindow` supplies it through `additionalArguments`.
- [ ] Expose `webTab.syncVisible`; main routes it through sender ownership and `syncVisibleViews`.
- [ ] In `desktop-bridge.ts`, replace singleton visibility bookkeeping with `visibleWebBounds: Map<string, Bounds>`. `WebTabPane` registers/removes its bounds, and one microtask/rAF scheduler sends the entire collection. This supports web+web compound panes without one call hiding the other.
- [ ] Make `visibleWebTab()` inspect the active group's resolved visible panes from `useCenterTabs`. Prefer `group.focusedId` when it is a visible web tab, then the first visible web pane, then an ungrouped active web tab.
- [ ] Run `npm --prefix desktop run check:webtabs && npm --prefix web run check:web-split`; pass.
- [ ] Commit: `feat(desktop): synchronize visible web panes`.

### Task 3: Add transient transfer mutations and a crash-recovery journal

**Files:**

- Modify `web/lib/state/center-tabs-store.ts`
- Modify `web/lib/session-store/index.ts`
- Create `web/lib/session-draft-persistence.ts`
- Modify `web/lib/state/files-shared.ts`
- Modify `web/lib/runtime-bridge/draft-channel-choice.ts`
- Create `web/lib/tab-transfer-journal.ts`
- Modify `web/scripts/check-web-split.mjs`

Import `CenterTabGroup` from `center-tab-groups.ts` and `CenterTabsPersistedPayload` from `center-tabs-store.ts`; do not redeclare either. The compound plan must export `CenterTabsPersistedPayload`.

**Transfer payload and exact chat-owned state:**

```ts
export interface TransferSourcePosition {
  windowId: string;
  kind: "tab" | "segment" | "group";
  groupId?: string;
  memberIndex?: number;
  memberIds?: string[];
  visibleIds?: string[];
  focusedId?: string;
}

export interface ChatTransferState {
  chatKey: string; // CenterTab.sessionId; provisional drafts use local_* unchanged
  composerDraft?: string; // useSessionStore.composerDrafts[chatKey]
  composerSettings?: ComposerSettings; // composerSettingsBySession[chatKey]
  pendingProjectId?: string; // pendingProjectsByChat[chatKey]
  draftChannelChoice?: PendingChannelChoice; // draftChannelChoiceFor(window, chatKey)
  wasActive: boolean; // activeChatKey === chatKey, not currentSessionId alone
  activeComposerInput?: string; // composerInput when wasActive
  activeComposerSettings?: ComposerSettings; // composerSettings when wasActive
}

export interface DesktopTransferPayload {
  tabs: CenterTab[];
  source: TransferSourcePosition;
  fileDrafts: Array<{ key: string; value: FileDraft }>;
  chats: ChatTransferState[];
}

export type TabDropPlacement =
  | { kind: "before"; targetTabId: string }
  | { kind: "merge"; targetTabId: string; groupId?: string; memberIndex?: number }
  | { kind: "after"; targetTabId: string }
  | { kind: "strip-end" };

export interface SessionTransferSnapshot {
  activeChatKey: string | null;
  currentSessionId: string | null;
  composerInput: string;
  composerSettings: ComposerSettings;
  composerDrafts: Record<string, string>;
  composerSettingsBySession: Record<string, ComposerSettings>;
  pendingProjectsByChat: Record<string, string>;
  draftChannelChoices: Record<string, PendingChannelChoice>;
}

export interface PersistedSessionDraftState {
  version: 1;
  composerDrafts: Record<string, string>;
  composerSettingsBySession: Record<string, ComposerSettings>;
  pendingProjectsByChat: Record<string, string>;
  draftChannelChoices: Record<string, PendingChannelChoice>;
}

export function sessionDraftStorageKey(windowId?: string | null): string {
  const resolvedWindowId = windowId || desktopWindowId() || "main";
  return `openprogram.sessionDraftState:${resolvedWindowId}`;
}
export function readSessionDraftState(): PersistedSessionDraftState;
export function replaceSessionDraftState(state: PersistedSessionDraftState): void;
export function updateSessionDraftState(
  update: (state: PersistedSessionDraftState) => PersistedSessionDraftState,
): PersistedSessionDraftState;

export interface SerializedWebViewBookkeeping {
  liveIds: string[];
  readyIds: string[];
  visibleBounds: Array<{ id: string; bounds: Bounds }>;
}

export interface TransferJournalEntry {
  version: 1;
  token: string;
  role: "source" | "destination";
  phase: "staged" | "committing" | "rolling-back";
  payload: DesktopTransferPayload;
  placement?: TabDropPlacement;
  beforeCenterTabs: CenterTabsPersistedPayload;
  afterCenterTabs: CenterTabsPersistedPayload;
  beforeSession: SessionTransferSnapshot;
  afterSession: SessionTransferSnapshot;
  beforeFileDrafts: Array<{ key: string; existed: boolean; value?: FileDraft }>;
  afterFileDrafts: Array<{ key: string; existed: boolean; value?: FileDraft }>;
  beforeBridge: SerializedWebViewBookkeeping;
  afterBridge: SerializedWebViewBookkeeping;
}

export interface TransferJournalFile {
  version: 1;
  entries: Record<string, TransferJournalEntry>;
}
```

Persist that file only at `openprogram.tabTransferJournal:<windowId>`. Store callback functions such as ready waiters only in the in-memory recovery object; the journal records ids/ready flags/bounds, and a reloaded renderer recreates waiters through the existing `ensure` path.

`writeTransferJournal` returns `false` on serialization/quota failure and verifies the token can be read back before mutation. A failed journal write aborts staging and reports destination/source failure to main; no transient state may change without a durable recovery entry.

Pending attachments are already profile-shared in IndexedDB under the stable chat key; transfer must not call `deleteAttachments` or mark the attachment owner closed. Long-paste entries are profile-shared in `composerPasteStore` and remain referenced by `composerDrafts`; transfer must not call `pasteStore.retainOnly`. These stores are not duplicated into the payload.

**Store/session APIs:**

```ts
validateTransferredTabs(payload: DesktopTransferPayload, placement: TabDropPlacement):
  | { ok: true; after: CenterTabsPersistedPayload }
  | { ok: false; reason: "duplicate" | "group-full" | "invalid"; duplicateId?: string };
insertTransferredTabs(payload: DesktopTransferPayload, placement: TabDropPlacement, options: { persist: false }): { ok: boolean; before: CenterTabsPersistedPayload; after: CenterTabsPersistedPayload };
removeTransferredTabs(ids: string[], options: { persist: false }): { ok: boolean; empty: boolean; before: CenterTabsPersistedPayload; after: CenterTabsPersistedPayload };
replaceCenterTabsPayload(payload: CenterTabsPersistedPayload, options: { persist: boolean }): void;
persistCurrentCenterTabsPayload(): void;

snapshotSessionTransfer(chatKeys: string[]): SessionTransferSnapshot;
applySessionTransfer(snapshot: SessionTransferSnapshot, options: { persist: boolean }): void;
persistCurrentSessionTransfer(chatKeys: string[]): void;
```

`persist:false` changes Zustand/in-memory maps only. It must not write `centerTabs:<windowId>` or `openprogram.sessionDraftState:<windowId>`. Every keyed map still belongs in the journal so reload can reconstruct transient state.

`session-draft-persistence.ts` becomes the normal durable owner of the four keyed maps. Main-window first read migrates legacy `composerDrafts` and `composerSettings` blobs into `openprogram.sessionDraftState:main`, then removes only those two legacy keys after the new value round-trips. Secondary windows start with an empty state and receive entries only through normal edits or committed transfer. `useSessionStore` composer/pending-project setters and `setDraftChannelChoice`/`dropDraftChannelChoice`/`switchDraftChannelChoice` all update this single per-window value without overwriting fields owned by the other module.

All operations are atomic for `payload.tabs`. A whole group transfers all members in member order or none. A two-member group may merge into one normal tab; any result above three rejects before journal or transient mutation.

- [ ] Add pure journal tests to `check-web-split.mjs`: write/read/update/delete two token entries under one window key; corrupt JSON returns an empty versioned journal; another `windowId` cannot see it.
- [ ] Force `localStorage.setItem` to throw and to silently fail read-back. Assert journal write returns false, no `{persist:false}` mutation runs, and the transaction takes its normal rejection/rollback path.
- [ ] Create main and secondary query-isolated stores. Assert main migrates legacy data into `centerTabs:main`, secondary uses `centerTabs:secondary`, both payloads include groups/split, and `CenterTabsPersistedPayload` is exported.
- [ ] Stage destination insertion with `{persist:false}`. Assert Zustand shows the transferred members but `centerTabs:secondary` and `openprogram.sessionDraftState:secondary` remain byte-for-byte unchanged; only the destination journal contains before/after.
- [ ] Stage source removal with `{persist:false}`. Assert no close tombstone, NTP fallback, `dropChatDraft`, `deleteAttachments`, channel deletion, paste GC, or durable payload change occurs.
- [ ] Build a draft session with `activeChatKey="local_one"`, `composerInput`, `composerDrafts.local_one`, `composerSettings`, `composerSettingsBySession.local_one`, `pendingProjectsByChat.local_one`, and a keyed `PendingChannelChoice`. Snapshot, stage transfer, roll back, and assert every field deep-equals its original value.
- [ ] Exercise normal persistence outside transfer: set composer text/settings, pending project, and keyed channel choice in window A; construct a fresh A store and assert all four maps restore from `openprogram.sessionDraftState:A`. Construct B and assert it is isolated. Verify legacy composer blobs migrate only into main once.
- [ ] Commit a draft transfer A→B, discard both renderer stores/journals, and reload. Assert A no longer contains that chat key and B restores its composer draft, settings, pending project, and channel choice from its normal `openprogram.sessionDraftState:<windowId>` key. Rollback instead preserves A and leaves B unchanged.
- [ ] Insert a whole two-member group before/after/merge; assert order, `visibleIds`, `focusedId`, contiguity, and source `memberIndex` metadata. Reject duplicate/fourth-member cases before journal/draft changes.
- [ ] Simulate renderer reload by discarding all in-memory stores/maps while retaining committed storage plus the journal. Test recovery for main statuses `committed`, `awaiting-source`, `destination-staged`, `rolled-back`, and `stale`:
  - `committed`: idempotently apply/persist `after*`, clear accepted state and journal;
  - destination pre-commit: reapply `after*` with `persist:false`, rebuild `acceptedTransfers`, and await commit/undo;
  - source `awaiting-source`: reapply transient `after*`, rebuild recovery, and resume `sourceRemoved`;
  - source merely `prepared`, `rolled-back`, or `stale`: apply/persist `before*`, clear accepted state and journal.
- [ ] Simulate crashes (a) after journal write before mutation, (b) after transient mutation, (c) after phase becomes `committing` before durable writes, and (d) after durable writes before journal deletion. Recovery must converge to `before*` for non-committed main status and `after*` for committed status, then delete the journal exactly once.
- [ ] Run `npm --prefix web run check:web-split`; expect missing transient/journal APIs.
- [ ] Implement journal writes before any provisional mutation. On commit set `phase="committing"`, persist `afterCenterTabs` and write the four `afterSession` maps through `replaceSessionDraftState`, then delete accepted-map and journal entries. On rollback set `phase="rolling-back"`, restore `beforeCenterTabs` plus `beforeSession` normal persistence, then delete accepted-map and journal entries. Each step is idempotent.
- [ ] The shared active-session effect observes committed/staged active tab changes and calls existing `activateSession`; the pathname effect continues to call `setCurrentConv` or `setCurrentDraft` using `activeChatKey` for provisional drafts.
- [ ] Run `npm --prefix web run check:web-split && npm --prefix web run check:multi-draft`; pass.
- [ ] Commit: `feat(desktop): journal provisional tab transfers`.

### Task 4: Implement the pre-commit main-process transaction

**Files:**

- Modify `desktop/main.js`
- Modify `desktop/scripts/check-webtab-navigation.js`

**States and commit boundary:**

```text
prepared
  -> destination-staged       (native records reparented; destination renderer inserted provisionally)
  -> awaiting-source          (destination acknowledged staging; main requests source removal)
  -> committed                (source reports ok; main immediately clears timer/token)

Any state before committed -> rolling-back -> deleted
committed -> never rolling-back
```

`TransferTransaction` stores `token`, `sourceId`, `destinationId`, payload, only the native records that actually exist, their source snapshots, locked record ids, status, timer, and optional detached window id. A web tab with no record has metadata in the transaction but no reparent operation.

**IPC:**

- `tab-transfer:prepare` is synchronous (`ipcMain.on` + `event.returnValue`) and returns an opaque token.
- `tab-transfer:inspect`, `accept`, `reject`, `status`, `journal-opened`, `journal-finalized`, `destination-ready`, `source-removed`, `destination-undone`, `cancel`, `detach`, and `claim-pending` are async handlers.
- Events: `tab-transfer:remove-source`, `tab-transfer:undo-destination`, `tab-transfer:finalize-orphaned`, `tab-transfer:committed`, `tab-transfer:rejected`, and `tab-transfer:rolled-back`.

- [ ] Build an executable fake-window transaction harness in `check-webtab-navigation.js`. Use two records, fake timers, fake renderer callbacks, and an ordered trace array.
- [ ] Test `tab-transfer-store.js` against a `mkdtemp` directory: atomic save/load, truncated/corrupt primary fallback to an empty versioned file, and failure before rename retaining the prior decision. Restart the VM/main coordinator from disk and assert `status(token)` still returns its terminal decision.
- [ ] Success trace must be exactly: destination validation, native destination ownership, destination provisional insertion, destination-ready, source reversible removal, main committed, destination/source durable finalize. Assert the timeout and active token are cleared at main committed, a non-rollback `committed` receipt remains, and it disappears only after both source and destination call `journal-finalized` and both acknowledgements are durably recorded.
- [ ] Invoke timeout after committed and call rollback manually; assert neither changes owner/store trace. `rollbackTransfer` must reject `status === "committed"`.
- [ ] Source-failure trace must include source recovery, destination undo acknowledgement, native reparent-to-source, and token deletion. Assert destination tabs/imported drafts/bookkeeping are absent after undo.
- [ ] Destination rollback trace must be exactly: `forgetTransferredWebView`/clear ready+bounds, stale store-subscription `webtab:destroy` rejected by main, transient store/session undo, destination-undone acknowledgement, native reparent-to-source, source bridge restore. Assert `webContents.close()` is never called.
- [ ] Race test: advance timeout while the source callback is pending, then deliver source `ok=true`. Main returns `false`; fake source restores its recovery; native owner is source. This explicitly prevents “source deleted, main only reparents view”.
- [ ] Simulate crash after source `journal-finalized` is durably acknowledged but before destination acknowledgement. Reload main from `tab-transfers.json`; assert status remains committed/rolled-back, `finalizedRoles=["source"]`, source acknowledgement is idempotent, destination can acknowledge, and only that second durable acknowledgement deletes the decision. Repeat with destination first.
- [ ] Mark a committed source as empty. Assert `sourceRemoved(ok=true)` does not close it; a destination finalization does not close it; only a successful durable source `journal-finalized` acknowledgement closes it. Force the acknowledgement write to fail and assert the window remains open for retry.
- [ ] Destroy the destination after its journal opens, then roll back. Assert main delegates `finalize-orphaned(destination,destinationWindowId)` to the source renderer, the destination's keyed journal is restored/deleted, the delegated acknowledgement is durable, and the terminal decision is removed. Repeat with a destroyed source and with both renderers gone; in the last case restart main plus one renderer and complete both orphan roles through `pending-terminal`. A role that never called `journal-opened` must not appear in `requiredRoles`.
- [ ] Test destination failure, token reuse, cancellation, 15-second expiry, source/destination close, and a failed second-record reparent. Atomic reparent restores the first record if the second fails.
- [ ] Test `reject`: duplicate rejection activates/reuses the destination id, returns `reason="duplicate"` plus `duplicateId`, notifies source, clears its prepared coordinator, and deletes the token without reparenting. Full-group rejection returns `reason="group-full"`, leaves both stores unchanged, notifies source, and deletes the token.
- [ ] Test web metadata with no source native record: prepare/accept/commit succeeds with `records=[]`; destination first visibility calls existing `ensure` once. If a record exists, identity reparent assertions remain mandatory.
- [ ] Run `npm --prefix desktop run check:webtabs`; expect missing transaction failures.
- [ ] Add `validateTransferPayload(ctx, payload)`: accept one to three unique tabs; allow only known tab kinds; cap ids/titles at 4 KiB, URL/path/project/session fields at 16 KiB, and each draft value at 2 MiB; require group metadata to match those ids; and derive `sourceId` from `event.sender` instead of trusting `payload.source.windowId`. For each web id, include `ctx.views.get(id)` when present and reject only when an existing record is owned elsewhere; absence is valid.
- [ ] Implement `reparentRecords(source,target,records)` and `restoreRecords(...)` with snapshots for bounds, visibility, and each context's `visibleViewIds`. Move the same objects; never load or close them.
- [ ] Mark every moved native id as transaction-locked from accept through commit/rollback. All ordinary source or destination `webtab:destroy`/hide/bounds/navigation handlers reject locked ids; transaction helpers alone may forget/reparent them. Clear locks only after final commit or native restore.
- [ ] `tab-transfer:reject` is allowed only for the destination context that inspected the opaque token. It sends the reason/duplicate id to source, clears the timer/token, and performs no native/store mutation. `tab-transfer:status` returns only `{ status, sourceId, destinationId }` needed by a journal owner; unrelated window ids receive `null`.
- [ ] `destination-ready(ok=true)` sets `awaiting-source` and sends `remove-source`. It does not consume the token.
- [ ] `source-removed(ok=true)` first atomically writes the `committed` decision to `tab-transfers.json`. Only after that succeeds, clear the timer/active transaction and send destination/source `committed`; show a detached destination, but do not close an empty source yet. If durable decision write fails, return false and take the pre-commit rollback path. There is no rollback-capable state after the committed decision reaches disk.
- [ ] `source-removed(ok=false)` calls pre-commit rollback. A stale/raced `source-removed` returns `false`; renderer is responsible for immediate local recovery.
- [ ] Pre-commit rollback first atomically writes a `rolled-back` decision whose `requiredRoles` are the roles recorded by `journal-opened`, then sends `undo-destination` and waits for the renderer to forget bridge bookkeeping and undo transient store/session state before `destination-undone`. Only then restore native records/source bookkeeping, close an uncommitted hidden window, and clear active timer/token/locks. If the destination is destroyed or misses the 2-second undo deadline, restore native ownership directly and enqueue its required journal role for orphan finalization by a surviving renderer; do not mark it finalized merely because the window disappeared.
- [ ] Load `tab-transfers.json` when main initializes its coordinator. `status(token)` consults active transfers, then durable decisions, and returns results only to source/destination participants or a renderer assigned orphan cleanup. `journal-finalized(token, role)` atomically adds the role to `finalizedRoles` before returning true. If `sourceEmpty` and the acknowledged role is source, close that source window only after this durable write. When every dynamic required role is present, atomically delete the decision. A pre-mutation rejection has no journal/decision and is deleted immediately after notification.
- [ ] Add `pending-terminal(windowId)` to return both the caller's unfinished roles and orphaned roles assigned for cleanup, including the journal owner's `windowId`. It lets a reloaded renderer finish an acknowledgement if it already cleared its journal immediately before crashing and lets any live renderer durably finalize a destroyed participant's keyed journal. The renderer verifies normal committed storage matches the decision outcome before acknowledging.
- [ ] Run the behavior harness; every trace passes.
- [ ] Commit: `feat(desktop): transact tab transfers`.

### Task 5: Expose synchronous preparation, renderer staging, recovery, and cleanup

**Files:**

- Modify `desktop/preload.js`
- Modify `web/lib/desktop-bridge.ts`
- Modify `web/lib/tab-drag-coordinator.ts`
- Modify `web/scripts/check-web-split.mjs`
- Modify `desktop/scripts/check-webtab-navigation.js`

**Preload API:**

```ts
interface DesktopTabTransferApi {
  prepare(payload: DesktopTransferPayload): string;
  inspect(token: string): Promise<DesktopTransferPayload | null>;
  accept(token: string): Promise<DesktopTransferPayload>;
  reject(token: string, reason: "duplicate" | "group-full" | "invalid", duplicateId?: string): Promise<boolean>;
  status(token: string): Promise<{ status: string; sourceId: string; destinationId: string | null } | null>;
  journalOpened(token: string, role: "source" | "destination"): Promise<boolean>;
  journalFinalized(token: string, role: "source" | "destination"): Promise<boolean>;
  destinationReady(token: string, ok: boolean): Promise<boolean>;
  sourceRemoved(token: string, ok: boolean, empty: boolean): Promise<boolean>;
  destinationUndone(token: string, ok: boolean): Promise<boolean>;
  cancel(token: string): Promise<boolean>;
  detach(token: string): Promise<boolean>;
  claimPending(windowId: string): Promise<string | null>;
  pendingTerminal(windowId: string): Promise<Array<{
    token: string;
    status: "committed" | "rolled-back";
    role: "source" | "destination";
    ownerWindowId: string;
    orphaned: boolean;
  }>>;
  onRejected(cb: (token: string, reason: string, duplicateId?: string) => void): () => void;
}
```

`prepare` uses `ipcRenderer.sendSync("tab-transfer:prepare", payload)`. It is called from pointer/mouse down or the keyboard Move-to-new-window command, never after `dragstart` begins.

**Renderer staging records:**

```ts
interface AcceptedTransfer {
  token: string;
  insertedIds: string[];
  journal: TransferJournalEntry;
  inMemoryBridgeRecovery: WebViewBookkeepingRecovery;
}
const acceptedTransfers = new Map<string, AcceptedTransfer>();
```

- [ ] Add behavior tests, not only source checks:
  - pointer preparation calls fake `prepare` once and stores the returned token;
  - immediate `dragstart` reads the same token and writes both MIME/text entries synchronously;
  - destination staging writes the full journal before applying any `{persist:false}` state, while committed payload/draft keys remain unchanged;
  - destination rollback first forgets bridge ids/bounds, observes main reject a stale ordinary destroy, restores journal `before*`, and deletes `acceptedTransfers[token]` plus journal;
  - destination commit writes journal `after*`, then deletes `acceptedTransfers[token]` plus journal;
  - source removal failure and stale main response restore journal `before*` and bridge bookkeeping;
  - successful source/main commit writes journal `after*` and clears source recovery exactly once;
  - simulated reload reconstructs accepted/source state from journal according to `status(token)`.
- [ ] Add a structure rejection for `async ... onDragStart` and for `await ... setData` ordering.
- [ ] Run web/desktop checks; expect missing bridge methods.
- [ ] Expose preload IPC with callback subscriptions for remove-source, undo-destination, committed, rejected, and rolled-back. Listener registration returns cleanup functions.
- [ ] On destination preview, run `validateTransferredTabs` before `accept`. For duplicate, activate the existing destination tab through `setActive` and the shared session-route effect, then call `reject(token,"duplicate",duplicateId)`. For a full group call `reject(token,"group-full")`. Do not create a journal or import state for either rejection.
- [ ] On valid destination accept: call main `accept`; derive complete before/after center/session/file/bridge snapshots; write and read back the destination journal; call `journalOpened(token,"destination")`; only then apply `after*` with `{persist:false}`, record `acceptedTransfers`, and call `destinationReady(true)`. Any local failure follows the fixed destination undo sequence and calls `destinationReady(false)`.
- [ ] On `remove-source`: derive before/after snapshots, write and read back the source journal, call `journalOpened(token,"source")`, snapshot in-memory ready waiters, and apply center/session/bridge `after*` with `{persist:false}`. Invoke `sourceRemoved`. If removal or main acknowledgement is false, apply journal `before*` and restore bridge state. If true, call one shared `finalizeTransferJournal(token,"source","after")`: set `committing`, persist `after*`, clear recovery/journal, then call `journalFinalized`. Do not close the source renderer locally; main closes an empty source only after that durable acknowledgement.
- [ ] On `undo-destination`, first call `forgetTransferredWebView` for every moved existing record and clear destination ready ids/visible bounds. Any store subscription's ordinary destroy is expected and must be rejected by main's lock. Then apply journal `beforeCenterTabs`, `beforeSession`, and file-draft snapshots with `{persist:false}`, delete `acceptedTransfers`, acknowledge `destinationUndone`, and finally delete the journal after main reports rollback.
- [ ] On `committed`, call the same `finalizeTransferJournal(token,role,"after")`. On normal `rolled-back`, apply/persist journal `before*`, clear accepted/recovery state and the journal, then call `journalFinalized(token,role)`; this acknowledgement is mandatory for both roles. If rollback happened before the source created a journal, source verifies its normal payload/session state is unchanged and still acknowledges its role. On `rejected`, clear the matching prepared coordinator immediately and announce the reason. On renderer cleanup, cancel prepared transactions and remove listeners, but leave unresolved journal entries for startup recovery.
- [ ] Run `npm --prefix web run check:web-split && npm --prefix desktop run check:webtabs`; pass.
- [ ] Commit: `feat(desktop): coordinate reversible renderer transfer`.

### Task 6: Use the same drag coordinator for same-window and cross-window placement

**Files:**

- Modify `web/components/center-tabs/center-tab-strip.tsx`
- Modify `web/lib/tab-drag-coordinator.ts`
- Modify `web/components/center-tabs/center-tabs.module.css`
- Modify `web/scripts/check-center-tabs.mjs`
- Modify `web/scripts/check-compound-tabs.mjs`

- [ ] Extend the coordinator behavior check with desktop preparation. Pointer down builds one payload with:

```ts
{
  tabs,
  source: {
    windowId,
    kind,
    groupId,
    memberIndex,
    memberIds: group?.memberIds,
    visibleIds: group?.visibleIds,
    focusedId: group?.focusedId,
  },
  fileDrafts,
  chats,
}
```

For `kind:"group"`, `tabs` contains all group members in member order. For a segment it contains one tab and exact source position metadata.

- [ ] Pointer/mouse down on a tab target, segment target, or neutral group handle calls synchronous `bridge.tabTransfer.prepare(payload)` and stores `{subject, transferToken, started:false, cancelled:false, committed:false}` in the compound plan's coordinator. Register a one-shot window pointerup/mouseup handler. If release/click occurs before `dragstart`, immediately call `cancel(token)` and clear the coordinator; behavior tests assert no prepared token remains. Close buttons are `draggable={false}`, stop pointer/mouse-down propagation, and never call prepare.
- [ ] `onDragStart` is non-async: call coordinator `start()`, remove the release-cancel listener, and serialize the existing token into `application/x-openprogram-tab-transfer` and `text/plain`.
- [ ] If no prepared token exists, prevent the drag and announce failure. Escape calls `cancel(token)`, marks the same coordinator cancelled, and clears drop markers.
- [ ] Every drop calls `resolveTabDropIntent(rect, clientX, target)` from the shared module. Do not infer merge solely from `closest()` and do not use different geometry across windows.
- [ ] Same-window drop compares `payload.source.windowId` to `bridge.windowId`, applies existing reorder/group/member actions, and cancels the prepared main token. Cross-window drop calls destination staging with the same before/merge/after placement.
- [ ] A whole-group edge drop moves the group as one strip entry. A whole-group merge transfers/inserts all members atomically and rejects a result above three. A segment edge drop preserves source group metadata for rollback.
- [ ] When a transferred web tab had no source native record, destination metadata insertion still succeeds. Its first visible `WebTabPane` invokes existing `ensure(tab.id,url)` and then participates in `syncVisible`; hidden metadata does not allocate a view early.
- [ ] Keyboard Move left/right on a segment calls `moveGroupMember` within the group; it does not always move the whole group. `Move to new window` uses the same synchronous `prepare` then `detach` sequence.
- [ ] `dragend` detaches only if the coordinator still owns the token, is not cancelled/committed, and `dropEffect === "none"`. Same-window/drop commit clears it. No second `dragRef` is allowed.
- [ ] Add transient opacity/before/merge/after markers; clear on leave/drop/end/Escape.
- [ ] Run `npm --prefix web run check:compound-tabs`, `npm --prefix web run check:center-tabs`, and `npm --prefix web run build`; pass.
- [ ] Commit: `feat(desktop): transfer tabs with unified drag geometry`.

### Task 7: Add renderer-ready detach claiming

**Files:**

- Modify `desktop/main.js`
- Modify `desktop/preload.js`
- Modify `web/lib/desktop-bridge.ts`
- Modify `desktop/scripts/check-webtab-navigation.js`
- Modify `web/scripts/check-web-split.mjs`

- [ ] Add a behavior test that creates a hidden window with a pending token, fires a simulated `did-finish-load` without a renderer claim, and asserts nothing is accepted/sent. After store/session hydration, transfer-listener installation, and journal recovery, call `claimPending(windowId)`; assert it returns the token and destination staging starts. Reload the hidden renderer and claim again while still pre-commit; assert the journal reconstructs transient state and the valid token is returned rather than lost.
- [ ] Add tests that another window id cannot claim the token, a committed/expired token returns null, and rollback closes the hidden window.
- [ ] Run desktop/web checks; expect missing `claimPending` behavior.
- [ ] `detach(token)` validates prepared ownership, creates `show:false` window with a new `windowId`, and records `pendingTransferToken` on its context/transaction. Do not register `did-finish-load` transfer delivery.
- [ ] Renderer startup order is fixed: hydrate committed center/session storage; register transfer event listeners; read `openprogram.tabTransferJournal:<windowId>`; query `status(token)` for every entry and apply the Task 3 recovery table; rebuild `acceptedTransfers` for a live destination entry; then query `pendingTerminal(windowId)`. For its own role, idempotently acknowledge a terminal decision whose journal was already cleared before a crash. For an orphan role, open the journal and normal keys using the supplied owner `windowId`, apply before/after, delete that keyed journal, and acknowledge the orphan role. Only after terminal/orphan cleanup reconcile ordinary native web views and call `claimPending`. A committed journal completes `after*`; rolled-back completes `before*`; a live pre-commit journal remains transient. Every terminal path clears its journal and calls `journalFinalized`, including normal rolled-back handlers.
- [ ] If `claimPending` returns a token not already represented by the recovered journal, preview/validate and stage it at strip end. If the journal already represents that token, resume it idempotently rather than inserting twice. This destination pull is safe across load timing and renderer reload.
- [ ] Show the hidden window only inside the source-success commit branch. Rollback/expiry closes it.
- [ ] Run `npm --prefix desktop run check:webtabs && npm --prefix web run check:web-split`; pass.
- [ ] Commit: `feat(desktop): claim detached transfers after renderer ready`.

### Task 8: Focused commands, IPC rejection, and complete automated gates

**Files:**

- Modify `desktop/main.js`
- Modify `web/lib/desktop-bridge.ts`
- Modify `desktop/scripts/check-webtab-navigation.js`
- Modify `web/scripts/check-web-split.mjs`

- [ ] Add an executable command-routing harness. Two fake windows register visible web panes and focus changes. Dispatch a menu command and a backend `webtab.command`; assert exactly one focused renderer handles each request and the returned target is its focused visible web pane.
- [ ] Send the same request id through two renderers; the main atomic claim registry allows only the focused owner. Assert a non-focused or destroyed renderer receives false.
- [ ] After a view moves, invoke every view IPC from the stale source sender. Assert navigate/bounds/show/hide/destroy all reject, the destination retains ownership, and `webContents.close` is never called.
- [ ] Run:

```bash
npm --prefix desktop run check:webtabs
npm --prefix web run check
npm --prefix web run build
python -m pytest tests/ --ignore=tests/test_provider_cli.py --ignore=tests/integration -q
```

- [ ] Confirm the compact-right-panel plan's automated check actually dispatches ArrowLeft/ArrowRight/Home/End to the separator and asserts width/clamping/`aria-valuenow`; a source-regex-only keyboard resize check is not an acceptance gate.
- [ ] Commit: `test(desktop): cover multiwindow ownership and routing`.

### Task 9: Live isolated acceptance with persistent local state

**Files:**

- Create `web/public/desktop-transfer-acceptance.html`
- Modify implementation only if acceptance exposes a defect.

- [ ] Add a deterministic local page at `/desktop-transfer-acceptance.html`. It must contain a text input, a 2400px scroll region, buttons that call `history.pushState`/back, and script that sets and displays a same-origin cookie plus `localStorage` session marker. This is the reproducible login/session-state surrogate; do not use a third-party account or the user's browser profile.
- [ ] Probe both development services before Electron. If either probe fails, start the existing `dev` worker with the established profile/ports, then repeat both probes; do not start/stop the default profile:

```bash
if ! curl -fsS http://127.0.0.1:18209/healthz >/dev/null || \
   ! curl -fsS http://127.0.0.1:18200/chat >/dev/null; then
  OPENPROGRAM_PROFILE=dev \
  OPENPROGRAM_BACKEND_PORT=18209 \
  OPENPROGRAM_WEB_PORT=18200 \
  openprogram worker start
fi

curl --fail --retry 30 --retry-delay 1 http://127.0.0.1:18209/healthz >/dev/null
curl --fail --retry 30 --retry-delay 1 http://127.0.0.1:18200/chat >/dev/null
curl --fail http://127.0.0.1:18200/desktop-transfer-acceptance.html >/dev/null
```

Expected: backend `18209/healthz` and frontend `18200/chat` both respond; stable `18100/18109` remain untouched.

- [ ] Start Electron with a real isolated user-data directory passed directly to Electron, not an unused environment variable:

```bash
acceptance_profile=$(mktemp -d /tmp/openprogram-multiwindow.XXXXXX)
cd desktop
OPENPROGRAM_WEB_PORT=18200 \
OPENPROGRAM_DESKTOP_URL=http://127.0.0.1:18200/chat \
./node_modules/.bin/electron . \
  --user-data-dir="$acceptance_profile" \
  --remote-debugging-port=9223
```

- [ ] Open the local acceptance page. Change input text, scroll, push history, and record through CDP: target id, `performance.timeOrigin`, input value, scrollY, history length, cookie, and localStorage marker.
- [ ] Transfer web+web, session+web, one segment, and a whole group to a second window; reorder before/after, merge, detach to a hidden-created window, and merge back. Confirm two web panes remain simultaneously visible where expected.
- [ ] After every move, assert the exact CDP target id and `timeOrigin` are unchanged, input/scroll/history/cookie/localStorage persist, and only one renderer/store owns each tab.
- [ ] Transfer focused and background sessions. Confirm destination URL, `useSessionStore.activeChatKey`, `currentSessionId`, `composerInput`, `composerSettings`, keyed composer draft/settings, pending project, and draft channel choice match the focused session; source fallback route/state matches its new active tab. Confirm attachment IndexedDB and long-paste references were neither deleted nor duplicated.
- [ ] Transfer a web metadata tab that has never mounted a native view. Confirm transfer succeeds, no reparent is attempted, and destination first visibility creates exactly one view through `ensure`.
- [ ] Force duplicate/full-group `reject`, destination insertion failure, source removal failure, destination undo, timeout-before-source-response, hidden destination close, and stale source/destination destroy IPC. Confirm duplicate activates the existing destination tab, source remains unchanged, bridge forgetting precedes store undo, `webContents.close` is never called for a locked record, and no accepted-transfer/journal entry remains after rollback.
- [ ] Complete a transfer, wait past 15 seconds, and try cancellation/rollback/token reuse. Confirm committed ownership never changes and the token is stale.
- [ ] Focus each window and invoke model browser control. Confirm it targets the focused visible web member, prioritizing the focused web pane when two are visible.
- [ ] Reload/crash a renderer at each journal phase: journal-written, transient-applied, destination-staged, awaiting-source, committing-before-persist, and persisted-before-journal-delete. Confirm main status resolves to exact before/after state, accepted maps/draft snapshots clear, `openprogram.tabTransferJournal:<windowId>` clears, `centerTabs:<windowId>` never contains an uncommitted provisional state, groups/split restore independently, and profile-wide bookmarks still update across windows.
- [ ] Run all automated gates again after any acceptance fix, then commit only the exact fix. Do not commit generated profile data.
