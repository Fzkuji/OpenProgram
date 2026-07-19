# Desktop Compound Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task by task. Use TDD and commit after each task.

**Goal:** Replace the desktop split marker with a two-or-three-member compound tab, preserve the existing close animation and split behavior, and support accessible same-window reorder, grouping, ungrouping, and member reordering.

**Architecture:** `CenterTab[]` remains the canonical strip order. `CenterTabGroup` is defined only in `center-tab-groups.ts`; group membership, visible member ids, focused member, and pane resolution are pure functions there. The store persists tabs, groups, active id, split compatibility state, and split ratio in one versioned per-window payload. `AppShell` renders the pane descriptors returned by the resolver, so two session members share one mounted chat pane and never create an empty second pane. One native-drag coordinator owns the payload, refs, geometry, and lifecycle used here and later by cross-window transfer.

**Tech stack:** TypeScript, React 18, Zustand 5, CSS Modules, native HTML Drag and Drop, Node `assert` behavior scripts. No new dependency.

## Non-negotiable invariants

- `CenterTabGroup` has one definition: `web/lib/state/center-tab-groups.ts`. Other modules import it.
- A group has two or three unique, live, contiguous members. One-member groups dissolve. A fourth member is rejected without mutation.
- `visibleIds` contains one or two group members and always contains `focusedId`.
- Group membership may contain any tab kinds. Pane count is derived by `resolveCenterTabPanes`, not by `visibleIds.length`.
- All visible session members share one `{ kind: "session" }` pane. Its `activeTabId` is the focused visible session, or the first visible session. Focusing it updates the router and `useSessionStore` through the existing session activation path.
- `showDivider` is `panes.length === 2`. `visibleIds.length === 2` must never create one rendered pane plus an unused divider.
- A group renders at most two panes. A hidden third member replaces the focused visible member; it is not destroyed.
- The singleton `<PageShell page="chat" />` remains mounted exactly once.
- Closing every normal or compound member uses the existing `closingIds` / `enteringIds` / `onExited` lifecycle. Store removal occurs only through `finishClose` after exit animation.
- Transfer removal is not implemented here and must not be routed through close. The multi-window plan adds a separate reversible path.
- Compound state is part of the same versioned `centerTabs:<windowId>` payload as tabs and split state. Never introduce `openprogram.centerTabGroups` or any other independent group key.
- Same-window and cross-window drag use the same `tab-drag-coordinator.ts`, payload type, prepared ref, and drop geometry. `dragstart` is synchronous and never writes `DataTransfer` after an `await`.
- A whole-group drag means all members. A segment drag means one member. Keyboard move on a segment changes its member order inside the group before moving the whole strip entry.
- Delete `splitPinned`, `data-split-pinned`, and the static accent line. Accent is transient focus/drop feedback only.

## Files

- Create `web/lib/state/center-tab-groups.ts`.
- Create `web/lib/tab-drag-coordinator.ts`.
- Create `web/scripts/check-compound-tabs.mjs`.
- Modify `web/lib/state/center-tabs-store.ts`.
- Modify `web/components/app-shell.tsx`.
- Modify `web/components/center-tabs/center-tab-strip.tsx`.
- Modify `web/components/center-tabs/center-tabs.module.css`.
- Modify `web/app/styles/base.css`.
- Modify `web/scripts/check-center-tabs.mjs`.
- Modify `web/scripts/check-web-split.mjs`.
- Modify `web/scripts/check-chat-ui.mjs`.
- Modify `web/package.json`.

### Task 1: Add the pure group model and pane resolver

**Files:**

- Create `web/lib/state/center-tab-groups.ts`
- Create `web/scripts/check-compound-tabs.mjs`

**Public API:**

```ts
export const MAX_CENTER_TAB_GROUP_MEMBERS = 3;
export const MAX_CENTER_TAB_PANES = 2;

export interface CenterTabGroup {
  id: string;
  memberIds: string[];
  visibleIds: string[];
  focusedId: string;
}

export type CenterTabPane =
  | { key: "session"; kind: "session"; activeTabId: string; memberIds: string[] }
  | { key: string; kind: "tab"; tabId: string };

export interface CenterTabLayout {
  tabIds: string[];
  groups: CenterTabGroup[];
}

export function normalizeCenterTabLayout(layout: CenterTabLayout): CenterTabLayout;
export function findCenterTabGroup(groups: readonly CenterTabGroup[], tabId: string): CenterTabGroup | undefined;
export function focusCenterTabGroupMember(layout: CenterTabLayout, groupId: string, memberId: string): CenterTabLayout;
export function groupCenterTabs(layout: CenterTabLayout, sourceId: string, targetId: string, memberIndex: number, newGroupId: string): { layout: CenterTabLayout; accepted: boolean };
export function ungroupCenterTab(layout: CenterTabLayout, tabId: string, beforeId?: string | null): CenterTabLayout;
export function moveCenterTab(layout: CenterTabLayout, tabId: string, beforeId: string | null): CenterTabLayout;
export function moveCenterTabGroup(layout: CenterTabLayout, groupId: string, beforeId: string | null): CenterTabLayout;
export function moveCenterTabGroupMember(layout: CenterTabLayout, groupId: string, memberId: string, toIndex: number): CenterTabLayout;
export function centerTabStripEntries(layout: CenterTabLayout): CenterTabStripEntry[];
export function resolveCenterTabPanes(group: CenterTabGroup | undefined, tabs: readonly CenterTab[], activeId: string | null): CenterTabPane[];
```

- [ ] Write `check-compound-tabs.mjs` first. Import the TypeScript module with `node --experimental-strip-types` and assert these behaviors:

```js
const tabs = [
  { id: "s:a", kind: "session", title: "A", sessionId: "a" },
  { id: "s:b", kind: "session", title: "B", sessionId: "b" },
  { id: "w:one", kind: "web", title: "One", url: "https://one.test/" },
  { id: "w:two", kind: "web", title: "Two", url: "https://two.test/" },
];

const normalized = normalizeCenterTabLayout({
  tabIds: tabs.map((tab) => tab.id),
  groups: [{
    id: "g:one",
    memberIds: ["s:a", "w:one", "w:two", "missing"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "missing",
  }],
});
assert.deepEqual(normalized.groups[0].memberIds, ["s:a", "w:one", "w:two"]);
assert.equal(normalized.groups[0].visibleIds.includes(normalized.groups[0].focusedId), true);

const hiddenFocused = focusCenterTabGroupMember(normalized, "g:one", "w:two");
assert.deepEqual(hiddenFocused.groups[0].visibleIds, ["w:two", "w:one"]);
assert.equal(hiddenFocused.groups[0].focusedId, "w:two");

const sessionsOnly = {
  id: "g:sessions",
  memberIds: ["s:a", "s:b"],
  visibleIds: ["s:a", "s:b"],
  focusedId: "s:b",
};
assert.deepEqual(resolveCenterTabPanes(sessionsOnly, tabs, "s:b"), [{
  key: "session",
  kind: "session",
  activeTabId: "s:b",
  memberIds: ["s:a", "s:b"],
}]);

const sessionAndWeb = { ...sessionsOnly, memberIds: ["s:a", "w:one"], visibleIds: ["s:a", "w:one"], focusedId: "w:one" };
assert.deepEqual(resolveCenterTabPanes(sessionAndWeb, tabs, "w:one").map((pane) => pane.kind), ["session", "tab"]);

const movedMember = moveCenterTabGroupMember({
  tabIds: ["s:a", "w:one", "w:two"],
  groups: [{ id: "g:one", memberIds: ["s:a", "w:one", "w:two"], visibleIds: ["s:a", "w:one"], focusedId: "w:one" }],
}, "g:one", "w:two", 0);
assert.deepEqual(movedMember.groups[0].memberIds, ["w:two", "s:a", "w:one"]);
assert.deepEqual(movedMember.tabIds, ["w:two", "s:a", "w:one"]);
assert.equal(movedMember.groups[0].visibleIds.includes(movedMember.groups[0].focusedId), true);
```

Also assert one-member dissolution, member contiguity, duplicate removal, fourth-member rejection without mutation, whole-group movement, and ungroup placement.

- [ ] Run `cd web && node --no-warnings --experimental-strip-types scripts/check-compound-tabs.mjs`. It must fail with `ERR_MODULE_NOT_FOUND`.
- [ ] Implement the module immutably. Every mutating helper returns `normalizeCenterTabLayout(...)`. Normalization must preserve member order, clamp to three, remove missing/claimed ids, make members contiguous, clamp visible ids to two, and choose a `focusedId` from `visibleIds`.
- [ ] Implement `resolveCenterTabPanes` by iterating `visibleIds` in order, adding the session pane once and normal panes by tab id. For multiple visible sessions, set the one session pane's `activeTabId` to `focusedId` when it is a session, otherwise the first visible session. Slice the result to two panes.
- [ ] Rerun the script; expect `compound-tabs pure checks passed`.
- [ ] Commit: `feat(desktop): add compound tab model`.

### Task 2: Move tabs, groups, and split state into one per-window payload

**Files:**

- Modify `web/lib/state/center-tabs-store.ts`
- Modify `web/scripts/check-compound-tabs.mjs`
- Modify `web/scripts/check-web-split.mjs`
- Modify `web/package.json`

**Persistence contract:**

```ts
export interface CenterTabsPersistedPayload {
  version: 2;
  tabs: CenterTab[];
  activeId: string | null;
  groups: CenterTabGroup[];
  splitWebTabId: string | null;
  splitRatio: number;
}

function desktopWindowId(): string | null;
function centerTabsStorageKey(windowId = desktopWindowId()): string {
  return windowId ? `centerTabs:${windowId}` : "centerTabs";
}
function readCenterTabsPayload(): CenterTabsPersistedPayload;
function persistCenterTabsPayload(state: Pick<CenterTabsState, "tabs" | "activeId" | "groups" | "splitWebTabId" | "splitRatio">): void;
```

The helper may read `window.openprogramDesktop?.windowId`; until the multi-window preload supplies it, desktop defaults to `"main"`. Browser-only use remains on `centerTabs`. On first read, migrate the existing `centerTabs` plus `openprogram.webSplit` values into the unified payload, then stop writing the split key. The multi-window plan extends this helper for secondary windows; it does not add another group type or storage payload.

**Store additions:** `groups`, `moveTab`, `moveGroup`, `moveGroupMember`, `groupTab`, `ungroupTab`, and `focusGroupMember`.

- [ ] Extend `check-compound-tabs.mjs` with a fake `localStorage` and `window.openprogramDesktop = { isDesktop: true, windowId: "main" }`. Seed the two legacy keys, import a query-isolated store, and assert one `centerTabs:main` JSON object contains `version`, `tabs`, `activeId`, `groups`, `splitWebTabId`, and `splitRatio`. Assert neither a write nor a read references `openprogram.centerTabGroups`.
- [ ] Exercise `setSplitWebTab("w:one")`: it must group the active session and web tab, set both visible, preserve the active session, and persist one payload.
- [ ] Add a third tab, focus it, and assert it replaces the prior `focusedId`, keeps membership, and persists `focusedId` inside `visibleIds`.
- [ ] Exercise `moveGroupMember`; it must reorder both `memberIds` and the corresponding contiguous portion of `tabs`.
- [ ] Close a grouped member through the existing store close action after the check has simulated animation completion. Assert the remaining one-member group dissolves and split compatibility id clears when applicable.
- [ ] Run `npm --prefix web run check:compound-tabs` and `npm --prefix web run check:web-split`; expect failures for missing state/actions.
- [ ] Replace `readPersisted`, `persist`, `readPersistedSplit`, and `persistSplit` with the unified helpers. Dirty flags still clear on restore. Invalid group members normalize through Task 1.
- [ ] Route every existing tab mutation through one `persistCenterTabsPayload` call. Do not leave a mutation that persists tabs while omitting groups or split state.
- [ ] Update `setActive` to focus a grouped member first, ensuring `focusedId` remains visible. Update `openWebTabInSplit` and non-null `setSplitWebTab` to create/reuse the same session+web group. Null split ungrouping keeps other valid groups.
- [ ] Add `"check:compound-tabs": "node --no-warnings --experimental-strip-types scripts/check-compound-tabs.mjs"` to `web/package.json` and include it in `npm run check`.
- [ ] Run `npm --prefix web run check:compound-tabs`, `npm --prefix web run check:web-split`, and `npm --prefix web run check:multi-draft`; all must pass.
- [ ] Commit: `feat(desktop): persist compound workspace state`.

### Task 3: Resolve panes before rendering

**Files:**

- Modify `web/components/app-shell.tsx`
- Modify `web/app/styles/base.css`
- Modify `web/scripts/check-web-split.mjs`

- [ ] Add a behavior section to `check-web-split.mjs` that calls `resolveCenterTabPanes` for session+session, session+web, web+web, and a hidden-third replacement. Assert pane lengths `1`, `2`, `2`, and at most `2`; assert the session+session descriptor selects the focused session.
- [ ] Add structural assertions that `AppShell` calls `resolveCenterTabPanes`, derives `showDivider` from `panes.length === 2`, and contains exactly one `<PageShell page="chat" />`. Reject `visibleTabs.length === 2` as a divider condition.
- [ ] In `AppShell`, select `tabs`, `groups`, and `activeId`; find the active group and call the resolver. Below the existing minimum split width, render only the focused/active pane without mutating the group or ratio.
- [ ] Render `{ kind: "session" }` with the existing singleton chat wrapper. Do not map session members to multiple `PageShell` instances. Render `{ kind: "tab" }` through the existing file/web/NTP components.
- [ ] Keep the existing 360/480 minimum, 6 DIP divider, preferred/effective ratio separation, measurement scheduler, pointer resize, and keyboard resize. Only render the divider between two actual pane descriptors.
- [ ] When a session member becomes focused, invoke the existing `activateSession` route path; the pathname effect must continue calling `useSessionStore.setCurrentConv` or `setCurrentDraft`. Move that activation into one `activeId` effect in `CenterTabStrip` so imported and pointer-activated sessions use the same path; remove duplicate direct calls.
- [ ] For web+web groups, mount two `WebTabPane` instances. The multi-window plan changes native visibility from one id to a set so both receive bounds and remain visible.
- [ ] Run `npm --prefix web run check:web-split && npm --prefix web run build`; expect pass and exactly one chat shell.
- [ ] Commit: `feat(desktop): resolve compound panes`.

### Task 4: Render compound segments without breaking close animation

**Files:**

- Modify `web/components/center-tabs/center-tab-strip.tsx`
- Modify `web/components/center-tabs/center-tabs.module.css`
- Modify `web/scripts/check-center-tabs.mjs`
- Modify `web/scripts/check-chat-ui.mjs`

**Required component contract:**

```ts
interface CompoundTabItemProps {
  group: CenterTabGroup;
  tabs: CenterTab[];
  activeId: string | null;
  enteringIds: ReadonlySet<string>;
  closingIds: ReadonlySet<string>;
  onActivate(tab: CenterTab): void;
  onClose(event: React.SyntheticEvent, tab: CenterTab): void;
  onExited(tab: CenterTab): void;
}
```

- [ ] Add source-structure checks before editing:

```js
assert.match(strip, /function CompoundTabItem/);
assert.match(strip, /enteringIds\.has\(tab\.id\)/);
assert.match(strip, /closingIds\.has\(tab\.id\)/);
assert.match(strip, /onAnimationEnd/);
assert.match(strip, /onExited\(tab\)/);
assert.match(strip, /onExited=\{finishClose\}/);
assert.doesNotMatch(strip, /splitPinned|data-split-pinned/);
assert.doesNotMatch(css, /data-split-pinned|inset 0 2px 0 var\(--accent-blue\)/);
```

Also assert each `group.memberIds.map` renders a `role="tab"` target and a sibling close button. This is a required structural check because exit animation is DOM lifecycle behavior.

- [ ] Run `npm --prefix web run check:center-tabs && npm --prefix web run check:chat-ui`; expect failure.
- [ ] Derive strip entries with `centerTabStripEntries`. Render ordinary entries with the current `TabItem`; render grouped entries with `CompoundTabItem`.
- [ ] Pass the real `enteringIds`, `closingIds`, and `finishClose` into `CompoundTabItem`. Each segment applies the same enter/exit CSS class as `TabItem`. Its animation-end handler calls `onExited(tab)` only for that segment's exit animation. Do not remove a group member when the close button is clicked; `onTabClose` starts animation and `finishClose` performs the eventual close/cleanup.
- [ ] Reuse existing icon/title/dirty/close markup and accessible labels. Keep `aria-selected`, roving `tabIndex`, and independent close focus for every segment.
- [ ] Implement neutral widths: two members target 360 DIP; three target 440 DIP; internal divider is 1 DIP. Delete the pinned-line state and CSS entirely.
- [ ] Run both checks and `npm --prefix web run build`; all pass.
- [ ] Commit: `feat(desktop): render animated compound tabs`.

### Task 5: Add one prepared drag coordinator and same-window geometry

**Files:**

- Create `web/lib/tab-drag-coordinator.ts`
- Modify `web/components/center-tabs/center-tab-strip.tsx`
- Modify `web/components/center-tabs/center-tabs.module.css`
- Modify `web/scripts/check-compound-tabs.mjs`
- Modify `web/scripts/check-center-tabs.mjs`

**Shared types used by both plans:**

```ts
export type TabDragSubject =
  | { kind: "tab"; tabIds: [string] }
  | { kind: "segment"; tabIds: [string]; sourceGroup: CenterTabGroup; memberIndex: number }
  | { kind: "group"; tabIds: string[]; sourceGroup: CenterTabGroup };

export type TabDropIntent =
  | { mode: "before"; targetTabId: string }
  | { mode: "merge"; targetTabId: string; groupId?: string; memberIndex?: number }
  | { mode: "after"; targetTabId: string };

export interface PreparedTabDrag {
  subject: TabDragSubject;
  transferToken?: string;
  started: boolean;
  cancelled: boolean;
  committed: boolean;
}

export function createTabDragCoordinator(): {
  prepare(prepared: PreparedTabDrag): void;
  current(): PreparedTabDrag | null;
  start(): PreparedTabDrag | null;
  cancel(): PreparedTabDrag | null;
  commit(): PreparedTabDrag | null;
  clear(): void;
};
export function resolveTabDropIntent(rect: Pick<DOMRect, "left" | "width">, clientX: number, target: { tabId: string; groupId?: string; memberIndex?: number }): TabDropIntent;
```

`resolveTabDropIntent` uses the first 25% for `before`, middle 50% for `merge`, and final 25% for `after`. Both same-window and cross-window drops call this function.

- [ ] Add behavior assertions to `check-compound-tabs.mjs`: prepare returns the same payload synchronously; `start()` changes `started` exactly once; pointer release before `start()` cancels and clears the prepared entry; cancel and commit are single-use; clear removes it; exact x positions resolve to before/merge/after; a segment payload retains `memberIndex`, `visibleIds`, and `focusedId`; a group payload retains every member id.
- [ ] Add structural checks that `onPointerDown` or `onMouseDown` prepares the coordinator before `onDragStart`; the `onDragStart` body contains no `await`; and all `DataTransfer.setData(...)` calls use `dragCoordinator.current()` synchronously. Reject `async function ...DragStart`. Assert the close button stops pointer/mouse-down propagation and is not a drag-preparation target.
- [ ] On pointer/mouse down of a normal tab target, compound segment target, or neutral group handle, snapshot the subject into the shared coordinator and register a one-shot window `pointerup`/`mouseup` cancellation. On native `dragstart`, call `start()`, remove that release listener, and synchronously set `effectAllowed` plus MIME/text payload. If pointerup/click occurs while `started === false`, cancel immediately; a click without drag must not leave a token for its 15-second expiry. If no prepared subject exists, call `preventDefault()` and announce cancellation. Close buttons use `draggable={false}` and stop pointer/mouse-down propagation, so clicking close never prepares a transfer.
- [ ] Use `resolveTabDropIntent` for all targets. Edge drops reorder a normal tab or whole group. Segment edge drops first ungroup and then place the tab. Center drops create/insert a group. A full group rejects without mutation.
- [ ] When a segment is moved left/right while still inside its group, call `moveGroupMember`; only moving past the group boundary ungroups it. A whole-group keyboard action calls `moveGroup`.
- [ ] Do not create another drag ref or payload in the multi-window work. That plan adds a synchronously prepared main-process token to this same `PreparedTabDrag`.
- [ ] Use reduced opacity on the source and transient insert/merge markers. Clear them on drop, `dragend`, and Escape. No persistent accent.
- [ ] Run `npm --prefix web run check:compound-tabs`, `npm --prefix web run check:center-tabs`, and `npm --prefix web run build`; all pass.
- [ ] Commit: `feat(desktop): add unified tab drag coordinator`.

### Task 6: Complete keyboard and announcement behavior

**Files:**

- Modify `web/components/center-tabs/center-tab-strip.tsx`
- Modify `web/components/center-tabs/center-tabs.module.css`
- Modify `web/scripts/check-chat-ui.mjs`
- Modify `web/scripts/check-center-tabs.mjs`

- [ ] Add checks for ArrowLeft/ArrowRight/Home/End, `Shift+F10`, `Escape`, `role="menu"`, native menu buttons, `role="status" aria-live="polite"`, and focus return to the invoking segment.
- [ ] Keep normal roving focus across strip segments. Arrow movement changes focus only; menu Move left/right mutates order. For a segment, Move left/right first reorders within `memberIds`, and only crosses the group boundary by ungrouping.
- [ ] Menu actions are Move left, Move right, Add to split, Remove from group, and Move to new window. The last action is disabled until the multi-window bridge is present, then uses the same coordinator.
- [ ] Announce successful reorder/group/ungroup, fourth-member rejection, cancelled drag, and completed transfer. Escape cancels the prepared coordinator and clears target/menu state.
- [ ] Run `npm --prefix web run check:chat-ui`, `npm --prefix web run check:center-tabs`, `npm --prefix web run check`, and `npm --prefix web run build`.
- [ ] Commit: `feat(desktop): add accessible compound controls`.

## Final verification

- [ ] Run:

```bash
npm --prefix web run check
npm --prefix web run build
npm --prefix desktop run check:webtabs
```

- [ ] Probe both development services before Electron. If either probe fails, start only the established `dev` worker, then repeat both probes; do not alter stable `18100/18109`:

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
OPENPROGRAM_WEB_PORT=18200 npm --prefix desktop run dev
```

Expected: backend `18209/healthz` and frontend `18200/chat` both respond before Electron starts.

- [ ] Verify session+web, web+web, and session+session groups. Session+session must show one chat pane and no divider; focusing either session must update the URL and `useSessionStore` state.
- [ ] Add and focus a hidden third member; confirm it replaces the focused pane and a fourth is rejected.
- [ ] Close every compound segment and confirm its exit animation completes before removal and all existing dirty/draft cleanup remains intact.
- [ ] Drag a normal tab, segment, and whole group through before/merge/after targets. Confirm segment keyboard movement reorders members and whole-group movement preserves all members.
- [ ] Resize below and above the split minimum; confirm group membership, focused member, and preferred ratio persist in one `centerTabs:main` payload.
- [ ] Confirm there is no `openprogram.centerTabGroups`, `splitPinned`, static top accent, duplicate chat shell, or empty divider.
