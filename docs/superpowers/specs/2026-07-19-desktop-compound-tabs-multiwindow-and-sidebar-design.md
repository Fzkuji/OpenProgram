# Desktop Compound Tabs, Multi-window Transfer, and Compact Right Panel

## Scope

Complete the desktop workspace interaction on top of the existing split view and bookmark manager:

- replace the split tab's orange top line with a segmented compound tab;
- allow tab reordering, grouping, ungrouping, detaching into another OpenProgram window, and merging back;
- preserve live web-page state while a web tab moves between windows;
- restyle the right sidebar as a compact tool rail plus an adjacent rounded content panel;
- keep bookmark add, search, rename, delete, split-open, full-tab-open, and persistence available from that panel.

No new drag-and-drop dependency or backend bookmark schema is introduced.

## Compound-tab Model

A compound tab is one item in the top strip containing two or three segmented members:

```text
[[ New chat  × ] | [ Example Domain  × ]]  [ Other tab  × ]  +
```

- A normal tab keeps the current desktop width target.
- A two-member group targets 360 DIP; a three-member group targets 440 DIP.
- Each segment keeps its icon, title, independent close button, focus target, and drag handle.
- The group uses one neutral outer surface and 1 DIP internal dividers. The static orange `splitPinned` line is removed.
- Orange is reserved for keyboard focus and an active drop target. Normal grouping uses only background and text contrast.
- Closing or dragging out a member dissolves a group that has only one member left.

Group membership and visible layout are separate. A group contains at most three members, while split view displays at most two panes. A third member stays live in the group; selecting it replaces the most recently focused pane. This avoids forcing three narrow panes while preserving quick switching and later ungrouping.

The existing session-plus-web split becomes a two-member compound group automatically. Selecting either segment focuses its pane. Selecting a grouped member that is not currently visible replaces the last-focused pane without destroying the displaced tab or its state.

## Drag Interaction

- Dragging within the strip reorders a normal tab, a whole group, or one segment inside a group.
- Dropping a tab on the center of another tab creates a compound group. Dropping between segments inserts it at that position. A group rejects a fourth member.
- Dragging a segment outside its group ungroups it. Releasing it elsewhere in the same strip inserts it as a normal tab.
- Releasing a tab outside every OpenProgram window creates a new OpenProgram window containing that tab.
- Dropping on another OpenProgram window transfers the tab into that window. Dropping on a tab or group there merges it at the indicated position.
- A drag source uses reduced opacity only. Drop position and merge targets are shown only while dragging.
- `Escape` cancels a drag. `Shift+F10` exposes Move left, Move right, Add to split, Remove from group, and Move to new window so drag is not the only path.

## State and Persistence

The renderer remains the owner of its window's ordered `CenterTab[]`. Extend the existing center-tab state with:

```ts
interface CenterTabGroup {
  id: string;
  memberIds: string[]; // two or three ids in strip order
  visibleIds: string[]; // one or two ids
  focusedId: string;
}
```

Add actions for reorder, group, ungroup, insert transferred tab, and remove transferred tab. Normal close behavior keeps its current draft deletion and fallback rules. Transfer removal is a distinct action: it must not create session-close tombstones, delete drafts, destroy a web view, or insert a new-tab fallback before the destination acknowledges receipt.

Each BrowserWindow receives a stable `windowId` from the preload bridge. Renderer persistence is keyed by that id. The existing unkeyed payload migrates into the first window once; newly created windows start from the transfer payload they receive.

Bookmark storage remains profile-wide under `openprogram.bookmarks`, so every OpenProgram window sees the same manager and change event.

## Main-process Window Coordinator

Electron main owns a `Map<windowId, WindowContext>`. Each context contains its `BrowserWindow`, native web views, and visible web-view ids. The current single-window variables move into that context without changing the renderer-facing web-tab operations.

Web-tab transfer moves the same `WebContentsView` object between window content views. It does not call `loadURL`, so redirects, login state, form state, scroll position, SPA memory, and the CDP target stay unchanged.

Cross-window transfer is a short transaction:

1. source renderer asks main for an opaque transfer token containing the tab and its group position;
2. main identifies the destination window from the drop or creates one;
3. destination renderer validates and inserts the payload;
4. main reparents the native view when the tab is a web tab;
5. destination acknowledges; only then does source remove its tab;
6. timeout or rejection restores the source unchanged.

Duplicate tab ids in a destination are resolved by activating/reusing the existing tab and rejecting duplicate insertion. A source window closes only after a successful transfer leaves it empty.

Menu commands and `webtab.command` are handled only by the focused window. Native-view IPC verifies that `event.sender` owns the addressed view.

## Split Layout

- Split view continues to display two panes with the existing divider and preferred-ratio behavior.
- A compound group's `visibleIds` determines the panes. Session plus web uses the current singleton chat and native web pane.
- Selecting a hidden third member replaces the last-focused pane. Replaced native web views receive zero bounds but remain alive.
- Below the existing minimum split width, the group remains intact and the focused member displays full-width. Restoring width restores the prior two visible members.
- Moving a split member to another window updates both source and destination groups without closing or reloading either tab.

## Compact Right Panel

The right side uses two layout elements:

- a permanent 49 DIP icon-only tool rail;
- an optional rounded content panel immediately to its left.

The rail's header is 40 DIP high to align with the tab row. History, Bookmarks, and Files remain 32 DIP buttons with tooltips. The selected button uses the existing neutral selected background; there is no persistent accent line.

The content panel is non-modal and occupies layout space so it cannot be covered by a native `WebContentsView`. It defaults to 320 DIP, resizes from 280 to 560 DIP, has 8 DIP outer gaps, a 16 DIP radius, existing popover border/shadow tokens, and a 40 DIP header. Clicking the selected rail button or pressing `Escape` closes it and restores focus to the rail button.

The Codex activity screenshot's dotted execution timeline is not copied into Bookmarks or Files because those views are not timelines.

## Bookmark Manager Completion

The existing manager is retained and must remain reachable from the Bookmarks rail button in every chat session. It provides:

- search by title or URL;
- title and URL display;
- inline title rename;
- deletion;
- normal activation that opens beside chat when split is available;
- an explicit full-tab action;
- empty and no-result states;
- immediate updates after toolbar-star or new-tab-page changes;
- profile-local persistence across reloads and windows.

No bookmark folders, synchronization, import/export, or favicon network service are added. They are not needed to complete the requested save/find/manage/open workflow.

## Accessibility

- Group outer containers remain presentational; every segment is a native button or `role="tab"` with roving `tabIndex`.
- Arrow keys navigate segments and groups; close buttons remain separately focusable.
- The divider and panel resize handle expose vertical separators with `aria-valuenow` and keyboard increments.
- Drag completion, cancellation, grouping, ungrouping, and window transfer are announced through one polite live region.
- History, Bookmarks, and Files rail items are native buttons.

## Error Handling

- A failed destination insert or native-view reparent leaves the source tab and group unchanged.
- A destination window that closes during transfer causes rollback.
- Invalid persisted group ids and missing members are removed during restore; one-member groups dissolve.
- A stale transfer token is single-use and expires.
- A fourth drop on a full group is rejected visibly without modifying either side.
- Dirty file tabs and unsent draft sessions require an acknowledged transfer; ordinary close confirmation rules are unchanged.

## Validation

Automated checks must cover:

- stable reorder and group insertion order;
- the three-member limit and one-member dissolution;
- hidden-third-member pane replacement;
- transfer removal not creating close tombstones or fallbacks;
- destination acknowledgement and rollback;
- per-window persistence migration;
- focused-window command routing and IPC ownership checks;
- same-object `WebContentsView` reparenting;
- right-panel keyboard resize and native rail buttons;
- the complete bookmark manager workflow and cross-window update event.

Live Electron verification must confirm:

- the orange pinned line is absent and the split pair appears as one segmented compound tab;
- reorder, group, ungroup, detach, cross-window merge, and merge-back work by pointer and keyboard menu;
- a transferred real web page keeps its CDP target, `performance.timeOrigin`, form value, scroll position, and login/session state;
- closing the destination during transfer rolls back safely;
- split, narrow-window fallback, third-member replacement, and right-panel resizing preserve tabs;
- the Bookmarks rail can add, find, rename, split-open, full-open, delete, and retain a bookmark after reload;
- model browser control attaches only to the focused window's visible web pane.
