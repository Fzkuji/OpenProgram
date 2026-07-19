# Desktop Compound Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the desktop split tab's static accent line with an accessible two-or-three-member compound tab that supports same-window reorder, grouping, ungrouping, and the existing session-plus-web split without losing pane state.

**Architecture:** Keep `CenterTab[]` as the canonical tab data and strip order. Add a small pure group-layout module plus `CenterTabGroup[]`; every group's members are contiguous in `tabs`, each group has at most three members and at most two visible panes, and selecting a hidden member replaces the group's most recently focused visible pane. Zustand remains the renderer authority, while `AppShell` derives one- or two-pane rendering from the active group and keeps the singleton chat surface mounted.

**Tech Stack:** TypeScript, React 18, Next.js 14, Zustand 5, CSS Modules, native HTML Drag and Drop, Node `assert` check scripts, Electron `WebContentsView` through the existing desktop bridge.

## Global Constraints

- A compound group contains exactly two or three members; a fourth member is rejected without changing state.
- A group exposes one or two `visibleIds`; selecting a hidden third member replaces the currently `focusedId` pane in place.
- Group members are contiguous in canonical `tabs[]` order.
- Removing or closing a member automatically dissolves a group that would contain only one member.
- The existing session-plus-web split automatically appears as one compound tab.
- Split layout still renders at most two panes and retains the existing 360 DIP chat minimum, 480 DIP web minimum, 6 DIP divider, preferred ratio, and narrow-window fallback.
- Remove the old `splitPinned` prop, `data-split-pinned` attribute, and static accent top line. Accent colour is used only for focus and active drop targets.
- Support same-window normal-tab reorder, whole-group reorder, center-drop grouping, segment insertion, and dragging a segment out to ungroup.
- Preserve one singleton `<PageShell page="chat" />`; session tabs never create a second chat surface.
- Every segment retains icon, title, independent close control, `role="tab"`, roving `tabIndex`, and keyboard activation.
- Provide `Shift+F10` alternatives for move left, move right, add to split, and remove from group, plus one polite live region for drag/group announcements.
- Use the already installed React/Zustand stack and native HTML Drag and Drop. Do not add a drag-and-drop dependency.
- Keep ordinary close semantics unchanged: dirty confirmation, draft cleanup, attachment cleanup, tombstones, and final-NTP fallback still run only through the existing close path.
- This plan covers renderer compound tabs and same-window operations. Electron cross-window transfer and compact-right-panel work remain separate independently testable plans from the approved design specification.

## File Structure

- Create `web/lib/state/center-tab-groups.ts`: pure group normalization, ordering, focus replacement, grouping, ungrouping, and strip-entry derivation.
- Create `web/scripts/check-compound-tabs.mjs`: behavior checks for group invariants and Zustand integration.
- Modify `web/lib/state/center-tabs-store.ts`: persist groups, expose group actions, make split state create/dissolve a compound group, and keep close semantics group-aware.
- Modify `web/components/center-tabs/center-tab-strip.tsx`: render compound items, implement native same-window drag/drop, keyboard menu, and live announcements.
- Modify `web/components/center-tabs/center-tabs.module.css`: neutral compound surface, segmented members, internal dividers, and transient drag/drop visuals; delete the old pinned line.
- Modify `web/components/app-shell.tsx`: derive active group and render its one or two visible panes while retaining one chat surface.
- Modify `web/app/styles/base.css`: generalize the existing split-pane wrappers for compound members without changing divider geometry.
- Modify `web/scripts/check-center-tabs.mjs`: structural and accessibility assertions for compound rendering and removal of the old pinned marker.
- Modify `web/scripts/check-web-split.mjs`: behavior assertions for automatic session-plus-web grouping, hidden-third replacement, narrow fallback, and singleton chat.
- Modify `web/package.json`: add `check:compound-tabs` to the existing `npm run check` chain.

---

### Task 1: Pure compound-group layout model

**Files:**
- Create: `web/lib/state/center-tab-groups.ts`
- Create: `web/scripts/check-compound-tabs.mjs`

**Interfaces:**
- Consumes: ordered tab ids from `CenterTab[]`.
- Produces:
  - `MAX_CENTER_TAB_GROUP_MEMBERS = 3`
  - `MAX_CENTER_TAB_GROUP_VISIBLE = 2`
  - `CenterTabGroup`
  - `CenterTabLayout`
  - `CenterTabStripEntry`
  - `normalizeCenterTabLayout(layout): CenterTabLayout`
  - `findCenterTabGroup(groups, tabId): CenterTabGroup | undefined`
  - `focusCenterTabGroupMember(layout, groupId, memberId): CenterTabLayout`
  - `groupCenterTabs(layout, sourceId, targetId, memberIndex, newGroupId): { layout; accepted }`
  - `ungroupCenterTab(layout, tabId, beforeId?): CenterTabLayout`
  - `moveCenterTab(layout, tabId, beforeId): CenterTabLayout`
  - `moveCenterTabGroup(layout, groupId, beforeId): CenterTabLayout`
  - `centerTabStripEntries(layout): CenterTabStripEntry[]`

- [ ] **Step 1: Write the failing pure behavior check**

Create `web/scripts/check-compound-tabs.mjs` with these initial assertions:

```js
import assert from "node:assert/strict";

const groups = await import("../lib/state/center-tab-groups.ts");

const broken = groups.normalizeCenterTabLayout({
  tabIds: ["a", "b", "c", "d"],
  groups: [{
    id: "g:one",
    memberIds: ["a", "c"],
    visibleIds: ["a", "c", "d"],
    focusedId: "missing",
  }],
});
assert.deepEqual(broken.tabIds, ["a", "c", "b", "d"]);
assert.deepEqual(broken.groups, [{
  id: "g:one",
  memberIds: ["a", "c"],
  visibleIds: ["a", "c"],
  focusedId: "a",
}]);

const oneMember = groups.normalizeCenterTabLayout({
  tabIds: ["a", "b"],
  groups: [{ id: "g:one", memberIds: ["a"], visibleIds: ["a"], focusedId: "a" }],
});
assert.deepEqual(oneMember.groups, []);

let result = groups.groupCenterTabs(
  { tabIds: ["a", "b", "c", "d"], groups: [] },
  "b",
  "a",
  1,
  "g:ab",
);
assert.equal(result.accepted, true);
assert.deepEqual(result.layout.groups[0].memberIds, ["a", "b"]);
assert.deepEqual(result.layout.tabIds, ["a", "b", "c", "d"]);

result = groups.groupCenterTabs(result.layout, "c", "a", 2, "unused");
assert.equal(result.accepted, true);
assert.deepEqual(result.layout.groups[0].memberIds, ["a", "b", "c"]);
assert.deepEqual(result.layout.groups[0].visibleIds, ["a", "b"]);

const full = groups.groupCenterTabs(result.layout, "d", "a", 3, "unused");
assert.equal(full.accepted, false);
assert.deepEqual(full.layout, result.layout);

const focusedA = groups.focusCenterTabGroupMember(result.layout, "g:ab", "a");
const focusedC = groups.focusCenterTabGroupMember(focusedA, "g:ab", "c");
assert.deepEqual(focusedC.groups[0].visibleIds, ["c", "b"]);
assert.equal(focusedC.groups[0].focusedId, "c");

const ungroupedC = groups.ungroupCenterTab(focusedC, "c", "d");
assert.deepEqual(ungroupedC.groups[0].memberIds, ["a", "b"]);
assert.deepEqual(ungroupedC.tabIds, ["a", "b", "c", "d"]);
const dissolved = groups.ungroupCenterTab(ungroupedC, "b");
assert.deepEqual(dissolved.groups, []);

const moved = groups.moveCenterTab(
  { tabIds: ["a", "b", "c"], groups: [] },
  "c",
  "a",
);
assert.deepEqual(moved.tabIds, ["c", "a", "b"]);

const entries = groups.centerTabStripEntries(result.layout);
assert.deepEqual(entries.map((entry) => entry.id), ["group:g:ab", "tab:d"]);

console.log("compound-tabs pure checks passed");
```

- [ ] **Step 2: Run the check to verify it fails**

Run:

```bash
cd web
node --no-warnings --experimental-strip-types scripts/check-compound-tabs.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `lib/state/center-tab-groups.ts`.

- [ ] **Step 3: Implement the pure layout module**

Create `web/lib/state/center-tab-groups.ts` with the exact public model below. Keep all mutations immutable and run every returned layout through `normalizeCenterTabLayout`.

```ts
export const MAX_CENTER_TAB_GROUP_MEMBERS = 3;
export const MAX_CENTER_TAB_GROUP_VISIBLE = 2;

export interface CenterTabGroup {
  id: string;
  memberIds: string[];
  visibleIds: string[];
  focusedId: string;
}

export interface CenterTabLayout {
  tabIds: string[];
  groups: CenterTabGroup[];
}

export type CenterTabStripEntry =
  | { id: `tab:${string}`; kind: "tab"; tabId: string }
  | { id: `group:${string}`; kind: "group"; group: CenterTabGroup };

const unique = (ids: readonly string[]) => Array.from(new Set(ids));

function moveBlockBefore(
  tabIds: readonly string[],
  block: readonly string[],
  beforeId: string | null,
): string[] {
  const blockSet = new Set(block);
  const remaining = tabIds.filter((id) => !blockSet.has(id));
  const index = beforeId === null ? remaining.length : remaining.indexOf(beforeId);
  const at = index < 0 ? remaining.length : index;
  return [...remaining.slice(0, at), ...block, ...remaining.slice(at)];
}

export function normalizeCenterTabLayout(layout: CenterTabLayout): CenterTabLayout {
  let tabIds = unique(layout.tabIds);
  const alive = new Set(tabIds);
  const claimed = new Set<string>();
  const groups: CenterTabGroup[] = [];
  for (const candidate of layout.groups) {
    const memberIds = unique(candidate.memberIds).filter(
      (id) => alive.has(id) && !claimed.has(id),
    ).slice(0, MAX_CENTER_TAB_GROUP_MEMBERS);
    if (memberIds.length < 2) continue;
    const memberSet = new Set(memberIds);
    const first = Math.min(...memberIds.map((id) => tabIds.indexOf(id)));
    const beforeId = tabIds.slice(first).find((id) => !memberSet.has(id)) ?? null;
    tabIds = moveBlockBefore(tabIds, memberIds, beforeId);
    memberIds.forEach((id) => claimed.add(id));
    let visibleIds = unique(candidate.visibleIds)
      .filter((id) => memberIds.includes(id))
      .slice(0, MAX_CENTER_TAB_GROUP_VISIBLE);
    if (visibleIds.length === 0) {
      visibleIds = memberIds.slice(0, MAX_CENTER_TAB_GROUP_VISIBLE);
    }
    const focusedId = visibleIds.includes(candidate.focusedId)
      ? candidate.focusedId
      : visibleIds[0];
    groups.push({ ...candidate, memberIds, visibleIds, focusedId });
  }
  return { tabIds, groups };
}

export function findCenterTabGroup(
  groups: readonly CenterTabGroup[],
  tabId: string,
): CenterTabGroup | undefined {
  return groups.find((group) => group.memberIds.includes(tabId));
}

export function focusCenterTabGroupMember(
  layout: CenterTabLayout,
  groupId: string,
  memberId: string,
): CenterTabLayout {
  return normalizeCenterTabLayout({
    ...layout,
    groups: layout.groups.map((group) => {
      if (group.id !== groupId || !group.memberIds.includes(memberId)) return group;
      if (group.visibleIds.includes(memberId)) {
        return { ...group, focusedId: memberId };
      }
      const replaceAt = Math.max(0, group.visibleIds.indexOf(group.focusedId));
      const visibleIds = [...group.visibleIds];
      visibleIds[replaceAt] = memberId;
      return { ...group, visibleIds, focusedId: memberId };
    }),
  });
}

export function ungroupCenterTab(
  layout: CenterTabLayout,
  tabId: string,
  beforeId: string | null = null,
): CenterTabLayout {
  const source = findCenterTabGroup(layout.groups, tabId);
  if (!source) {
    return beforeId === null ? layout : moveCenterTab(layout, tabId, beforeId);
  }
  const groups = layout.groups.flatMap((group) => {
    if (group.id !== source.id) return [group];
    const memberIds = group.memberIds.filter((id) => id !== tabId);
    if (memberIds.length < 2) return [];
    const visibleIds = group.visibleIds.filter((id) => id !== tabId);
    return [{
      ...group,
      memberIds,
      visibleIds,
      focusedId: visibleIds.includes(group.focusedId)
        ? group.focusedId
        : visibleIds[0] ?? memberIds[0],
    }];
  });
  const tabIds = beforeId === null
    ? layout.tabIds
    : moveBlockBefore(layout.tabIds, [tabId], beforeId);
  return normalizeCenterTabLayout({ tabIds, groups });
}

export function moveCenterTab(
  layout: CenterTabLayout,
  tabId: string,
  beforeId: string | null,
): CenterTabLayout {
  const ungrouped = ungroupCenterTab(layout, tabId);
  return normalizeCenterTabLayout({
    ...ungrouped,
    tabIds: moveBlockBefore(ungrouped.tabIds, [tabId], beforeId),
  });
}

export function moveCenterTabGroup(
  layout: CenterTabLayout,
  groupId: string,
  beforeId: string | null,
): CenterTabLayout {
  const group = layout.groups.find((candidate) => candidate.id === groupId);
  if (!group) return layout;
  return normalizeCenterTabLayout({
    ...layout,
    tabIds: moveBlockBefore(layout.tabIds, group.memberIds, beforeId),
  });
}

export function groupCenterTabs(
  layout: CenterTabLayout,
  sourceId: string,
  targetId: string,
  memberIndex: number,
  newGroupId: string,
): { layout: CenterTabLayout; accepted: boolean } {
  if (sourceId === targetId) return { layout, accepted: false };
  const targetBefore = findCenterTabGroup(layout.groups, targetId);
  if (targetBefore && !targetBefore.memberIds.includes(sourceId)
      && targetBefore.memberIds.length >= MAX_CENTER_TAB_GROUP_MEMBERS) {
    return { layout, accepted: false };
  }
  const detached = ungroupCenterTab(layout, sourceId);
  const targetGroup = findCenterTabGroup(detached.groups, targetId);
  const targetMembers = targetGroup?.memberIds ?? [targetId];
  const at = Math.max(0, Math.min(memberIndex, targetMembers.length));
  const memberIds = [...targetMembers];
  memberIds.splice(at, 0, sourceId);
  if (memberIds.length > MAX_CENTER_TAB_GROUP_MEMBERS) {
    return { layout, accepted: false };
  }
  const group: CenterTabGroup = targetGroup
    ? { ...targetGroup, memberIds }
    : {
        id: targetBefore?.id ?? newGroupId,
        memberIds,
        visibleIds: memberIds.slice(0, MAX_CENTER_TAB_GROUP_VISIBLE),
        focusedId: targetId,
      };
  const groups = targetGroup
    ? detached.groups.map((candidate) => candidate.id === group.id ? group : candidate)
    : [...detached.groups, group];
  const memberSet = new Set(memberIds);
  const targetAt = Math.min(...targetMembers.map((id) => detached.tabIds.indexOf(id)));
  const beforeId = detached.tabIds.slice(Math.max(0, targetAt))
    .find((id) => !memberSet.has(id)) ?? null;
  return {
    accepted: true,
    layout: normalizeCenterTabLayout({
      tabIds: moveBlockBefore(detached.tabIds, memberIds, beforeId),
      groups,
    }),
  };
}

export function centerTabStripEntries(layout: CenterTabLayout): CenterTabStripEntry[] {
  const firstToGroup = new Map(layout.groups.map((group) => [group.memberIds[0], group]));
  const grouped = new Set(layout.groups.flatMap((group) => group.memberIds));
  return layout.tabIds.flatMap((tabId) => {
    const group = firstToGroup.get(tabId);
    if (group) return [{ id: `group:${group.id}` as const, kind: "group" as const, group }];
    if (grouped.has(tabId)) return [];
    return [{ id: `tab:${tabId}` as const, kind: "tab" as const, tabId }];
  });
}
```

When implementing, keep the exact exported names above. If the insertion-index arithmetic reveals an off-by-one through the check, fix the helper rather than weakening the assertion.

- [ ] **Step 4: Run the pure check to verify it passes**

Run:

```bash
cd web
node --no-warnings --experimental-strip-types scripts/check-compound-tabs.mjs
```

Expected: `compound-tabs pure checks passed`.

- [ ] **Step 5: Commit the pure model**

```bash
git add web/lib/state/center-tab-groups.ts web/scripts/check-compound-tabs.mjs
git commit -m "feat(desktop): add compound tab layout model"
```

### Task 2: Zustand group state, persistence, and split migration

**Files:**
- Modify: `web/lib/state/center-tabs-store.ts:18-589`
- Modify: `web/scripts/check-compound-tabs.mjs`
- Modify: `web/scripts/check-web-split.mjs:430-497`
- Modify: `web/package.json:5-12`

**Interfaces:**
- Consumes: all pure helpers from Task 1.
- Produces additions to `CenterTabsState`:
  - `groups: CenterTabGroup[]`
  - `moveTab(id, beforeId): void`
  - `moveGroup(groupId, beforeId): void`
  - `groupTab(sourceId, targetId, memberIndex?): boolean`
  - `ungroupTab(id, beforeId?): void`
  - `focusGroupMember(groupId, memberId): void`
- Preserves: `splitWebTabId`, `splitRatio`, `openWebTabInSplit`, and `setSplitWebTab` as compatibility APIs used by the desktop bridge and bookmark manager.

- [ ] **Step 1: Add failing Zustand and split behavior checks**

Append to `web/scripts/check-compound-tabs.mjs` after the pure checks:

```js
const values = new Map();
globalThis.window = {};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.crypto ??= (await import("node:crypto")).webcrypto;

const { useCenterTabs } = await import("../lib/state/center-tabs-store.ts?compound-state");
useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id: "w:one", kind: "web", title: "One", url: "https://one.example/" },
    { id: "w:two", kind: "web", title: "Two", url: "https://two.example/" },
    { id: "w:three", kind: "web", title: "Three", url: "https://three.example/" },
  ],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.44,
});

useCenterTabs.getState().setSplitWebTab("w:one");
let state = useCenterTabs.getState();
assert.equal(state.splitWebTabId, "w:one");
assert.deepEqual(state.groups[0].memberIds, ["s:chat", "w:one"]);
assert.deepEqual(state.groups[0].visibleIds, ["s:chat", "w:one"]);

assert.equal(state.groupTab("w:two", "w:one", 2), true);
state = useCenterTabs.getState();
assert.deepEqual(state.groups[0].memberIds, ["s:chat", "w:one", "w:two"]);
state.setActive("w:two");
state = useCenterTabs.getState();
assert.equal(state.activeId, "w:two");
assert.deepEqual(state.groups[0].visibleIds, ["s:chat", "w:two"]);
assert.equal(state.groupTab("w:three", "w:one", 3), false);
assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:chat", "w:one", "w:two"]);

state.ungroupTab("w:two");
state = useCenterTabs.getState();
assert.deepEqual(state.groups[0].memberIds, ["s:chat", "w:one"]);
state.closeTab("w:one");
state = useCenterTabs.getState();
assert.deepEqual(state.groups, []);
assert.equal(state.splitWebTabId, null);
assert.ok(values.has("openprogram.centerTabGroups"));
```

In `web/scripts/check-web-split.mjs`, immediately after the existing `openWebTabInSplit` assertions at lines 436–443, add:

```js
assert.equal(useCenterTabs.getState().groups.length, 1);
assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:chat", id]);
assert.deepEqual(useCenterTabs.getState().groups[0].visibleIds, ["s:chat", id]);
```

Add the script to `web/package.json`:

```json
"check": "npm run check:center-tabs && npm run check:compound-tabs && npm run check:bookmarks && npm run check:web-split && npm run check:multi-draft && npm run check:provisional-send && npm run check:chat-ui",
"check:compound-tabs": "node --no-warnings --experimental-strip-types scripts/check-compound-tabs.mjs",
```

- [ ] **Step 2: Run the checks to verify they fail**

Run:

```bash
cd web
npm run check:compound-tabs
npm run check:web-split
```

Expected: `check:compound-tabs` FAILS because `groups` and group actions do not exist; `check:web-split` FAILS because split creation has no compound group.

- [ ] **Step 3: Add persisted group state and actions**

In `center-tabs-store.ts`, import the Task 1 API and add a separate persistence key so the existing `centerTabs` payload and all current callers remain stable:

```ts
import {
  centerTabStripEntries,
  findCenterTabGroup,
  focusCenterTabGroupMember,
  groupCenterTabs,
  moveCenterTab,
  moveCenterTabGroup,
  normalizeCenterTabLayout,
  ungroupCenterTab,
  type CenterTabGroup,
} from "./center-tab-groups";

const GROUPS_STORAGE_KEY = "openprogram.centerTabGroups";

function readPersistedGroups(tabs: CenterTab[]): CenterTabGroup[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(GROUPS_STORAGE_KEY);
    const groups = raw ? JSON.parse(raw) as CenterTabGroup[] : [];
    return normalizeCenterTabLayout({
      tabIds: tabs.map((tab) => tab.id),
      groups: Array.isArray(groups) ? groups : [],
    }).groups;
  } catch {
    return [];
  }
}

function persistGroups(groups: CenterTabGroup[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(GROUPS_STORAGE_KEY, JSON.stringify(groups));
  } catch {
    /* group restore is optional; live tabs remain usable */
  }
}

function tabsInLayoutOrder(tabs: CenterTab[], tabIds: string[]): CenterTab[] {
  const byId = new Map(tabs.map((tab) => [tab.id, tab]));
  return tabIds.map((id) => byId.get(id)).filter((tab): tab is CenterTab => !!tab);
}

function nextGroupId(): string {
  return `g:${crypto.randomUUID()}`;
}
```

Extend `CenterTabsState` with the exact actions in the Interfaces block. Initialize `groups` from `readPersistedGroups(initial.tabs)`.

Implement one internal state adapter so all actions persist the same normalized result:

```ts
function applyLayout(
  s: CenterTabsState,
  layout: { tabIds: string[]; groups: CenterTabGroup[] },
): Pick<CenterTabsState, "tabs" | "groups"> {
  const normalized = normalizeCenterTabLayout(layout);
  const tabs = tabsInLayoutOrder(s.tabs, normalized.tabIds);
  persist({ tabs, activeId: s.activeId });
  persistGroups(normalized.groups);
  return { tabs, groups: normalized.groups };
}
```

Add the store actions with these exact bodies:

```ts
moveTab: (id, beforeId) => set((s) => applyLayout(s, moveCenterTab(
  { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups }, id, beforeId,
))),
moveGroup: (groupId, beforeId) => set((s) => applyLayout(s, moveCenterTabGroup(
  { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups }, groupId, beforeId,
))),
groupTab: (sourceId, targetId, memberIndex = Number.MAX_SAFE_INTEGER) => {
  let accepted = false;
  set((s) => {
    const result = groupCenterTabs(
      { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups },
      sourceId,
      targetId,
      memberIndex,
      nextGroupId(),
    );
    accepted = result.accepted;
    return result.accepted ? applyLayout(s, result.layout) : {};
  });
  return accepted;
},
ungroupTab: (id, beforeId = null) => set((s) => applyLayout(s, ungroupCenterTab(
  { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups }, id, beforeId,
))),
focusGroupMember: (groupId, memberId) => set((s) => {
  const layout = focusCenterTabGroupMember(
    { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups },
    groupId,
    memberId,
  );
  persistGroups(layout.groups);
  return { groups: layout.groups };
}),
```

Update `setActive` so activating a grouped member also applies hidden-third replacement before persistence:

```ts
setActive: (id) => set((s) => {
  if (!s.tabs.some((tab) => tab.id === id)) return {};
  const group = findCenterTabGroup(s.groups, id);
  const layout = group
    ? focusCenterTabGroupMember(
        { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups },
        group.id,
        id,
      )
    : { tabIds: s.tabs.map((tab) => tab.id), groups: s.groups };
  if (s.activeId === id && layout.groups === s.groups) return {};
  persist({ tabs: s.tabs, activeId: id });
  persistGroups(layout.groups);
  return { activeId: id, groups: layout.groups };
}),
```

In both `openWebTabInSplit` and non-null `setSplitWebTab`, find the active session tab, call `groupCenterTabs(..., webId, sessionId, 1, nextGroupId())`, and persist its result. On `setSplitWebTab(null)`, ungroup the old split web member and persist the remaining layout. Keep `splitWebTabId` for the existing desktop bridge.

In `closeTab`, after filtering `tabs`, call `normalizeCenterTabLayout` with the closing id removed, persist the normalized groups, and return them with `tabs`, `activeId`, and `splitWebTabId`. Do not route group removal through `onTabClose` or add new cleanup behavior.

- [ ] **Step 4: Run focused checks to verify they pass**

Run:

```bash
cd web
npm run check:compound-tabs
npm run check:web-split
npm run check:multi-draft
```

Expected:

```text
compound-tabs pure checks passed
web-split checks passed
multi-draft checks passed
```

- [ ] **Step 5: Commit Zustand integration**

```bash
git add web/lib/state/center-tabs-store.ts web/lib/state/center-tab-groups.ts web/scripts/check-compound-tabs.mjs web/scripts/check-web-split.mjs web/package.json
git commit -m "feat(desktop): persist compound tab groups"
```

### Task 3: Compound-aware split pane rendering

**Files:**
- Modify: `web/components/app-shell.tsx:456-624`
- Modify: `web/app/styles/base.css:352-377`
- Modify: `web/scripts/check-web-split.mjs:292-390`

**Interfaces:**
- Consumes: `groups`, `findCenterTabGroup`, `visibleIds`, `focusedId`, existing split-layout helpers.
- Produces: active compound groups render one or two panes; narrow windows render only `activeId` full-width without mutating group state.

- [ ] **Step 1: Add failing pane-resolution checks**

In `check-web-split.mjs`, replace the old assertion that `showSplit` requires `activeKind === "session"` with:

```js
assert.match(appShellSource, /const activeGroup = findCenterTabGroup\(groups, activeTab\?\.id \?\? ""\);/);
assert.match(appShellSource, /activeGroup\?\.visibleIds/);
assert.match(appShellSource, /const visibleTabs = splitAvailable[\s\S]*activeGroup[\s\S]*activeTab/);
assert.doesNotMatch(
  appShellSource,
  /isDesktop && activeKind === "session" && !!splitTab && splitAvailable/,
);
assert.equal(
  appShellSource.match(/<PageShell page="chat" \/>/g)?.length,
  1,
  "compound rendering must keep one chat shell",
);
assert.match(appShellSource, /visibleTabs\.map/);
assert.match(appShellSource, /focusedId/);
```

Extend the store behavior section with a three-member group and assert that selecting its hidden third member changes `visibleIds` but not `splitRatio` or membership.

- [ ] **Step 2: Run the split check to verify it fails**

Run:

```bash
cd web
npm run check:web-split
```

Expected: FAIL because `AppShell` still derives split visibility from `activeKind === "session"` and `splitWebTabId` alone.

- [ ] **Step 3: Derive panes from the active group**

Import `findCenterTabGroup` and `type CenterTab`. Select the full `tabs` array and `activeId` so a title or URL update in a visible background member rerenders its pane:

```ts
const tabs = useCenterTabs((s) => s.tabs);
const activeId = useCenterTabs((s) => s.activeId);
const groups = useCenterTabs((s) => s.groups);
const activeTab = tabs.find((tab) => tab.id === activeId);
const activeGroup = findCenterTabGroup(groups, activeTab?.id ?? "");
const groupTabs = activeGroup?.visibleIds
  .map((id) => tabs.find((tab) => tab.id === id))
  .filter((tab): tab is CenterTab => !!tab) ?? [];
const visibleTabs = isDesktop && activeGroup && splitAvailable
  ? groupTabs.slice(0, 2)
  : activeTab ? [activeTab] : [];
const showSplit = visibleTabs.length === 2;
```

Do not write the width-constrained `effectiveSplitRatio` back to the store. Preserve `centerBodyRef`, `createSplitLayoutMeasureScheduler`, divider pointer handling, and keyboard increments unchanged.

Replace the kind-specific single-pane block with a small local renderer. It must render a session member through the existing singleton `PageShell`, and render web/file/NTP members with their current components:

```tsx
function renderNonSessionPane(tab: CenterTab) {
  if (tab.kind === "file" && tab.projectId && tab.path) {
    return <FileTabPane key={tab.id} projectId={tab.projectId} path={tab.path} />;
  }
  if (tab.kind === "web") {
    return <WebTabPane key={tab.id} tabId={tab.id} url={tab.url ?? ""} />;
  }
  if (tab.kind === "ntp") return <NewTabPage key={tab.id} />;
  return null;
}
```

Render visible members in `visibleIds` order. Keep one dedicated chat wrapper and place it using CSS `order` equal to its visible index; map only non-session members through `renderNonSessionPane`. If two session members would otherwise be visible, render only the active/focused session in the singleton slot and let selecting the other session replace that slot. This preserves the one-chat invariant.

For two panes use the existing 6 DIP separator and width the first pane with `effectiveSplitRatio`. For one pane use `flex: 1`. Below 846 DIP, use `[activeTab]` without changing `groups`, `visibleIds`, or `splitRatio`; restoring width therefore restores the prior two members automatically.

Generalize the CSS names without changing values:

```css
.center-compound-pane {
  display: flex;
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
}
.center-compound-pane:first-of-type {
  flex: 0 0 auto;
}
.center-split-divider {
  width: 6px;
  flex: 0 0 6px;
  cursor: col-resize;
  touch-action: none;
  background: var(--border);
}
```

Retain the existing 360/480 minimum calculations in `split-layout.ts`; do not duplicate them in CSS or `AppShell`.

- [ ] **Step 4: Run split and build checks**

Run:

```bash
cd web
npm run check:web-split
npm run build
```

Expected: `web-split checks passed`; Next.js build exits 0; the check still finds exactly one `<PageShell page="chat" />`.

- [ ] **Step 5: Commit compound pane rendering**

```bash
git add web/components/app-shell.tsx web/app/styles/base.css web/scripts/check-web-split.mjs
git commit -m "feat(desktop): render compound tab panes"
```

### Task 4: Segmented compound-tab visuals and removal of the pinned line

**Files:**
- Modify: `web/components/center-tabs/center-tab-strip.tsx:47-452`
- Modify: `web/components/center-tabs/center-tabs.module.css:27-410`
- Modify: `web/scripts/check-center-tabs.mjs:16-145`
- Modify: `web/scripts/check-chat-ui.mjs:77-94`

**Interfaces:**
- Consumes: `groups`, `centerTabStripEntries`, existing `TabItem` activation/close callbacks.
- Produces: `CompoundTabItem` with two or three independently interactive `TabSegment`s; no `splitPinned` UI state.

- [ ] **Step 1: Add failing structural and CSS checks**

Append to `check-center-tabs.mjs`:

```js
assert.match(strip, /centerTabStripEntries/);
assert.match(strip, /function CompoundTabItem/);
assert.match(strip, /styles\.compoundTab/);
assert.match(strip, /styles\.compoundSegment/);
assert.match(strip, /group\.memberIds\.map/);
assert.doesNotMatch(strip, /splitPinned/);
assert.doesNotMatch(strip, /data-split-pinned/);
assert.doesNotMatch(css, /data-split-pinned/);
assert.match(
  css,
  /\.compoundTab\s*\{[^}]*width:\s*360px;[^}]*border-radius:\s*8px;/s,
);
assert.match(css, /\.compoundTab\[data-members="3"\][^{]*\{[^}]*width:\s*440px;/s);
assert.match(css, /\.compoundSegment \+ \.compoundSegment::before/);
assert.doesNotMatch(css, /inset 0 2px 0 var\(--accent-blue\)/);
```

Extend `check-chat-ui.mjs` so both normal tabs and compound segments retain `role="tab"`, `aria-selected`, roving `tabIndex`, and sibling close buttons.

- [ ] **Step 2: Run checks to verify they fail**

Run:

```bash
cd web
npm run check:center-tabs
npm run check:chat-ui
```

Expected: FAIL because the strip still passes `splitPinned` and CSS still paints the inset top line.

- [ ] **Step 3: Render groups as one strip entry with segmented members**

In `CenterTabStrip`, select `groups`, derive entries once, and render either the existing normal `TabItem` or a new `CompoundTabItem`:

```ts
const groups = useCenterTabs((s) => s.groups);
const entries = centerTabStripEntries({
  tabIds: tabs.map((tab) => tab.id),
  groups,
});
const tabById = new Map(tabs.map((tab) => [tab.id, tab]));
```

```tsx
{entries.map((entry) => entry.kind === "tab" ? (
  <TabItem
    key={entry.id}
    tab={tabById.get(entry.tabId)!}
    active={entry.tabId === activeId}
    enter={enteringIds.has(entry.tabId)}
    closing={closingIds.has(entry.tabId)}
    label={labelOf(tabById.get(entry.tabId)!)}
    closeLabel={text("Close tab", "关闭标签")}
    onClick={() => onTabClick(tabById.get(entry.tabId)!)}
    onClose={(event) => onTabClose(event, tabById.get(entry.tabId)!)}
    onExited={() => finishClose(tabById.get(entry.tabId)!)}
  />
) : (
  <CompoundTabItem
    key={entry.id}
    group={entry.group}
    tabs={entry.group.memberIds.map((id) => tabById.get(id)).filter((tab): tab is CenterTab => !!tab)}
    activeId={activeId}
    labelOf={labelOf}
    closeLabel={text("Close tab", "关闭标签")}
    onActivate={onTabClick}
    onClose={onTabClose}
  />
))}
```

`CompoundTabItem` must use a presentational outer `<div>` and one `role="tab"` target plus sibling close button per member. Reuse the current icon/title/close markup rather than introducing a second icon map. Give the outer element `data-members={tabs.length}` and the segment `data-focused={tab.id === group.focusedId || undefined}`. `aria-selected` remains `tab.id === activeId`.

Delete `splitWebTabId` selection from `CenterTabStrip`, the `splitPinned` prop and type, `data-split-pinned`, and the CSS rule at the old lines 187–189.

Add neutral compound CSS:

```css
.compoundTab {
  position: relative;
  display: flex;
  width: 360px;
  max-width: 360px;
  height: 30px;
  flex: 0 1 360px;
  min-width: 112px;
  overflow: hidden;
  border-radius: 8px;
  background: color-mix(in srgb, var(--bg-primary) 72%, transparent);
  -webkit-app-region: no-drag;
}
.compoundTab[data-members="3"] {
  width: 440px;
  max-width: 440px;
  flex-basis: 440px;
}
.compoundSegment {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1 1 0;
  padding: 0 5px 0 12px;
  color: var(--text-secondary);
}
.compoundSegment[data-focused="true"] {
  background: var(--bg-primary);
  color: var(--text-bright);
}
.compoundSegment + .compoundSegment::before {
  content: "";
  position: absolute;
  left: 0;
  top: 7px;
  bottom: 7px;
  width: 1px;
  background: var(--border);
}
.compoundSegment:has(.tabTarget:focus-visible) {
  outline: 2px solid var(--accent-blue);
  outline-offset: -2px;
  z-index: 2;
}
```

Do not add an always-visible accent border or top line.

- [ ] **Step 4: Run focused UI checks and build**

Run:

```bash
cd web
npm run check:center-tabs
npm run check:chat-ui
npm run build
```

Expected: both checks print their pass messages; build exits 0; searches for `splitPinned` and `data-split-pinned` in the strip and CSS return no matches.

- [ ] **Step 5: Commit segmented visuals**

```bash
git add web/components/center-tabs/center-tab-strip.tsx web/components/center-tabs/center-tabs.module.css web/scripts/check-center-tabs.mjs web/scripts/check-chat-ui.mjs
git commit -m "feat(desktop): render segmented compound tabs"
```

### Task 5: Native same-window reorder, grouping, and ungrouping

**Files:**
- Modify: `web/components/center-tabs/center-tab-strip.tsx`
- Modify: `web/components/center-tabs/center-tabs.module.css`
- Modify: `web/scripts/check-compound-tabs.mjs`
- Modify: `web/scripts/check-center-tabs.mjs`

**Interfaces:**
- Consumes: `moveTab`, `moveGroup`, `groupTab`, `ungroupTab`; strip/group/member data attributes from Task 4.
- Produces: native drag payload `{ kind: "tab" | "group" | "segment"; id: string }`, transient edge/merge target state, and committed operation announcements.

- [ ] **Step 1: Add failing drag-source checks**

Append to `check-center-tabs.mjs`:

```js
assert.match(strip, /application\/x-openprogram-center-tab/);
assert.match(strip, /dataTransfer\.effectAllowed = "move"/);
assert.match(strip, /dataTransfer\.setData/);
assert.match(strip, /onDragOver=/);
assert.match(strip, /onDrop=/);
assert.match(strip, /moveTab/);
assert.match(strip, /moveGroup/);
assert.match(strip, /groupTab/);
assert.match(strip, /ungroupTab/);
assert.match(css, /\.dragSource/);
assert.match(css, /\.dropMergeTarget/);
assert.match(css, /\.dropInsertTarget/);
```

Extend `check-compound-tabs.mjs` with store assertions for:

```js
// Normal-tab edge drop reorders canonical tabs.
state.moveTab("w:three", "s:chat");
assert.equal(useCenterTabs.getState().tabs[0].id, "w:three");

// Whole-group move keeps members contiguous and in member order.
const groupId = useCenterTabs.getState().groups[0].id;
state.moveGroup(groupId, null);
const afterGroupMove = useCenterTabs.getState();
const memberIndexes = afterGroupMove.groups[0].memberIds.map(
  (memberId) => afterGroupMove.tabs.findIndex((tab) => tab.id === memberId),
);
assert.deepEqual(memberIndexes, [memberIndexes[0], memberIndexes[0] + 1]);
```

- [ ] **Step 2: Run checks to verify they fail**

Run:

```bash
cd web
npm run check:compound-tabs
npm run check:center-tabs
```

Expected: FAIL because the strip exposes no native drag payload or drop handlers.

- [ ] **Step 3: Implement one native drag/drop path**

Use one MIME constant and one local state object; do not add a dependency:

```ts
const TAB_DRAG_MIME = "application/x-openprogram-center-tab";
type TabDragPayload = { kind: "tab" | "group" | "segment"; id: string };
type DropTarget = {
  entryId: string;
  targetTabId: string;
  mode: "before" | "merge" | "after";
  memberIndex?: number;
};

const [dragging, setDragging] = useState<TabDragPayload | null>(null);
const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
const dragCommittedRef = useRef(false);

function writeDrag(event: React.DragEvent, payload: TabDragPayload) {
  dragCommittedRef.current = false;
  setDragging(payload);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData(TAB_DRAG_MIME, JSON.stringify(payload));
  event.dataTransfer.setData("text/plain", payload.id);
}
```

Compute drop mode from the target rect: first 25% is `before`, middle 50% is `merge`, last 25% is `after`. For compound segments, derive `memberIndex` from the segment's index. Call `preventDefault()` only for a recognized in-app payload and set `dropEffect = "move"`.

On drop:

```ts
if (payload.kind === "group") {
  const beforeId = target.mode === "after" ? nextEntryFirstTabId : target.targetTabId;
  moveGroup(payload.id, beforeId);
} else if (target.mode === "merge") {
  groupTab(payload.id, target.targetTabId, target.memberIndex);
} else {
  if (payload.kind === "segment") ungroupTab(payload.id);
  const beforeId = target.mode === "after" ? nextEntryFirstTabId : target.targetTabId;
  moveTab(payload.id, beforeId);
}
dragCommittedRef.current = true;
setDragging(null);
setDropTarget(null);
```

Mark normal tabs and compound segments `draggable`; mark the compound outer surface draggable only from a small neutral group handle so segment close/activation remain reliable. A segment dragged to an edge first ungroups, then reorders as a normal tab. A center drop into a full three-member group calls `groupTab`, observes `false`, and leaves both source and target unchanged.

Add only transient visuals:

```css
.dragSource { opacity: 0.55; }
.dropMergeTarget { outline: 2px solid var(--accent-orange); outline-offset: -2px; }
.dropInsertTarget::after {
  content: "";
  position: absolute;
  top: 4px;
  bottom: 4px;
  width: 2px;
  background: var(--accent-orange);
}
```

Do not scale the source and do not leave an accent after `dragend`.

- [ ] **Step 4: Run drag/store checks and build**

Run:

```bash
cd web
npm run check:compound-tabs
npm run check:center-tabs
npm run build
```

Expected: compound and center-tab checks pass; build exits 0; `web/package.json` has no new dependency.

- [ ] **Step 5: Commit same-window drag/drop**

```bash
git add web/components/center-tabs/center-tab-strip.tsx web/components/center-tabs/center-tabs.module.css web/scripts/check-compound-tabs.mjs web/scripts/check-center-tabs.mjs
git commit -m "feat(desktop): add same-window tab grouping"
```

### Task 6: Keyboard commands, ARIA, and live announcements

**Files:**
- Modify: `web/components/center-tabs/center-tab-strip.tsx`
- Modify: `web/components/center-tabs/center-tabs.module.css`
- Modify: `web/scripts/check-chat-ui.mjs`
- Modify: `web/scripts/check-center-tabs.mjs`

**Interfaces:**
- Consumes: group actions and drag commit state from Tasks 2 and 5.
- Produces: `Shift+F10` menu, `Escape` cancellation, roving segment focus, and `role="status" aria-live="polite"` announcements.

- [ ] **Step 1: Add failing accessibility checks**

Append to `check-chat-ui.mjs`:

```js
assert.match(tabs, /e\.shiftKey && e\.key === "F10"/);
assert.match(tabs, /role="menu"/);
assert.match(tabs, /role="menuitem"/);
assert.match(tabs, /role="status"/);
assert.match(tabs, /aria-live="polite"/);
assert.match(tabs, /Move left/);
assert.match(tabs, /Move right/);
assert.match(tabs, /Add to split/);
assert.match(tabs, /Remove from group/);
assert.match(tabs, /e\.key === "Escape"/);
```

Add a `check-center-tabs.mjs` assertion that each compound outer container is presentational and each member retains `role="tab"` plus a sibling close button.

- [ ] **Step 2: Run accessibility checks to verify they fail**

Run:

```bash
cd web
npm run check:chat-ui
npm run check:center-tabs
```

Expected: FAIL because the strip has no keyboard operation menu or live region.

- [ ] **Step 3: Add the keyboard operation menu and announcements**

Add state with exact shape:

```ts
const [tabMenu, setTabMenu] = useState<{ tabId: string; x: number; y: number } | null>(null);
const [announcement, setAnnouncement] = useState("");
```

In `onTabListKeyDown`, preserve ArrowLeft/ArrowRight/Home/End behavior and handle `Shift+F10` before returning. Resolve the focused `[role="tab"][data-tab-id]`, open the menu at its bounding rect, and prevent the browser menu. Handle `Escape` by clearing `tabMenu`, `dragging`, and `dropTarget`; native drag cancellation still completes through `dragend`.

Render a menu with native buttons carrying `role="menuitem"`:

```tsx
{tabMenu ? (
  <div className={styles.tabMenu} role="menu" style={{ left: tabMenu.x, top: tabMenu.y }}>
    <button role="menuitem" onClick={() => moveRelative(tabMenu.tabId, -1)}>
      {text("Move left", "向左移动")}
    </button>
    <button role="menuitem" onClick={() => moveRelative(tabMenu.tabId, 1)}>
      {text("Move right", "向右移动")}
    </button>
    <button role="menuitem" onClick={() => addToSplit(tabMenu.tabId)}>
      {text("Add to split", "加入分屏")}
    </button>
    {findCenterTabGroup(useCenterTabs.getState().groups, tabMenu.tabId) ? (
      <button role="menuitem" onClick={() => removeFromGroup(tabMenu.tabId)}>
        {text("Remove from group", "移出组合")}
      </button>
    ) : null}
  </div>
) : null}
<span className={styles.srOnly} role="status" aria-live="polite" aria-atomic="true">
  {announcement}
</span>}
```

`moveRelative` must operate on `centerTabStripEntries`, moving a whole group when the selected member belongs to one and a normal tab otherwise. `addToSplit` calls `setSplitWebTab(tabId)` for a web tab while a session is active; otherwise it center-groups the selected tab with the current active tab when they differ. `removeFromGroup` calls `ungroupTab` and keeps that tab active.

Set `announcement` after every successful reorder, group, ungroup, rejected-full-group drop, and cancelled drag. Use literal localized results such as `Moved <title> left`, `Grouped <title>`, `Removed <title> from group`, `Group already has three tabs`, and `Tab move cancelled`.

On every tab target add `data-tab-id={tab.id}`. Keep `aria-selected={tab.id === activeId}` and `tabIndex={tab.id === activeId ? 0 : -1}`. The compound outer container has no interactive role.

Add menu and screen-reader CSS using existing tokens; no new colour:

```css
.tabMenu {
  position: fixed;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  min-width: 160px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-secondary);
  box-shadow: var(--shadow);
}
.tabMenu button {
  min-height: 30px;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  color: var(--text-primary);
  background: transparent;
  text-align: left;
}
.tabMenu button:hover,
.tabMenu button:focus-visible { background: var(--bg-hover); }
.srOnly {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
```

- [ ] **Step 4: Run accessibility and full web checks**

Run:

```bash
cd web
npm run check:chat-ui
npm run check:center-tabs
npm run check
npm run build
```

Expected: all check scripts print their pass messages and build exits 0.

- [ ] **Step 5: Commit keyboard and ARIA support**

```bash
git add web/components/center-tabs/center-tab-strip.tsx web/components/center-tabs/center-tabs.module.css web/scripts/check-chat-ui.mjs web/scripts/check-center-tabs.mjs
git commit -m "feat(desktop): add accessible compound tab controls"
```

## Final Verification

- [ ] Run the complete automated gates:

```bash
cd web
npm run check
npm run build
cd ../desktop
npm run check:webtabs
```

Expected: every command exits 0.

- [ ] Start the development desktop app on the development ports only:

```bash
cd web
npm run dev -- --port 18200
```

In another terminal:

```bash
cd desktop
OPENPROGRAM_WEB_PORT=18200 npm run dev
```

- [ ] Verify in the visible Electron window:

1. Open a chat and a real web page, click `Open split view`, and confirm the two members appear in one neutral segmented 360 DIP compound tab with no top accent line.
2. Confirm both panes remain visible when either segment is selected and the divider still changes the preferred ratio.
3. Add a third web tab to the group; confirm the group targets 440 DIP, still renders only two panes, and selecting the hidden member replaces the previously focused pane without reloading the other pane.
4. Attempt to add a fourth member; confirm no state changes and the polite announcement reports the three-tab limit.
5. Drag a normal tab before/after another item, drag it onto the center of another tab to group, drag a segment out to ungroup, and drag the neutral group handle to reorder the whole group.
6. Remove a member from a two-member group and confirm the remaining tab becomes normal automatically.
7. Resize below 846 DIP; confirm only the active member is full-width. Restore width and confirm the prior two `visibleIds` return with the same split ratio.
8. Use ArrowLeft/ArrowRight/Home/End across normal tabs and compound segments. Open `Shift+F10`, execute move and remove-from-group actions, and confirm focus remains visible.
9. Confirm closing a dirty file still prompts, closing a draft still clears its draft/attachments, and closing the last ordinary tab still creates one NTP.
10. Confirm the bookmark manager can still open a bookmark beside chat through `openWebTabInSplit` and that this creates the same compound representation.
