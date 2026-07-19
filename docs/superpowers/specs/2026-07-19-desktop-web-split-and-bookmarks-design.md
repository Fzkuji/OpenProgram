# Desktop Web Split View and Bookmark Management

## Scope

Add a desktop-only split view that keeps the current chat visible while one existing web tab remains visible and controllable beside it. Complete the existing bookmark feature with a persistent, discoverable manager in the right sidebar.

The split view reuses the current center-tab store, `WebTabPane`, Electron `WebContentsView`, and browser-control bridge. It does not create a second `BrowserWindow`, a detached floating window, or more than one simultaneously visible web page.

## User-visible Layout

When a session tab is active and a web tab is pinned for split view, the center body contains:

```text
chat and composer | draggable divider | web toolbar and live page | right rail
```

- Chat starts at 44% and the web page at 56% of the center body.
- The divider changes the ratio and persists the last ratio.
- Chat keeps a minimum usable width of 360 DIP; the web pane keeps 480 DIP.
- Opening split view collapses an expanded right detail panel to its 49 DIP icon rail. The rail remains usable.
- Reopening a right-sidebar view keeps the split visible while both panes still satisfy their minimum widths. If the reduced center body is too narrow, the split pane is temporarily not mounted; closing the sidebar restores it. Exiting split does not restore a previously expanded right-sidebar view.
- If the center body is narrower than the two minimum widths plus the divider, the split pane is temporarily not mounted. Its pinned tab and ratio remain stored and return when enough width is available. Opening a bookmark collapses the right panel before showing the split page.

## Split-view Interaction

- A split-view button is added to the desktop web toolbar. Activating it pins that web tab and restores the session identified by the current chat route/session state as the primary tab. If that session tab no longer exists, it creates a draft with the existing new-session path. No separate session-MRU state is added.
- Only session tabs display beside the pinned web tab. Selecting a file, new-tab page, or any web tab uses the existing single-pane view. Returning to a session restores the pinned split page.
- Selecting the pinned web tab in the top strip displays it full-width through the existing tab behavior. Returning to a session restores the split without reloading the page.
- The split web toolbar includes an exit-split action. Exiting split does not close the web tab.
- Closing the pinned web tab clears the split reference. Closing or switching chat sessions does not close or reload the pinned page.
- The pinned web tab receives a small split indicator in the tab strip; the primary session tab remains the active tab.

## State and Persistence

Extend the existing center-tab state with:

- `splitWebTabId: string | null`
- `splitRatio: number`
- actions to pin, unpin, and resize the split web tab
- an action that opens or reuses a web tab for split view without changing `activeId`

One small `openprogram.webSplit` local-storage payload stores both fields. Keeping it separate avoids changing every existing center-tab persistence call. Restore clamps invalid ratios and clears a split id that no longer identifies an existing web tab. `closeTab` clears and persists `splitWebTabId` when necessary.

No separate split-view store or backend schema is introduced.

## WebContentsView Lifecycle

`AppShell` mounts one `WebTabPane` in one of two positions: the existing full-width web pane or the right split pane. It never mounts the same web tab twice.

The current `ResizeObserver` in `DesktopWebTabPane` continues reporting the native view bounds, so resizing the divider requires no new Electron bounds protocol. The main process may retain its single `visibleViewId` rule because this design shows only one native web view at a time.

Mounting a pane calls `ensure` and `show`, but no longer calls `navigate` unconditionally. `ensure` loads the URL only when the native view is first created; later navigation remains driven by a real URL change. Moving the same tab between split and full-width positions therefore keeps its page, redirects, scroll state, and form state.

The divider and all split controls remain outside the native view bounds. `DesktopWebTabPane` observes existing Radix dialogs and explicit `data-native-view-occluder` overlays. While an overlay covers the page, or while the split is below its minimum width, the native view receives zero bounds; the last non-zero bounds are restored after the overlay closes. Zero bounds are used instead of visibility alone because an agent `activate` call can show the native view again.

## Agent Browser Control

The desktop `webtab.command` handler resolves the controllable page in this order:

1. a full-width active web tab;
2. a visible split web tab beside an active session;
3. otherwise no visible target.

While a session is active and the split has enough space, `op=open` opens or reuses the requested URL in the split pane without changing the session route or `activeId`. The desktop bridge keeps renderer-local ready state per web-tab id. `DesktopWebTabPane` marks its id ready after sending non-zero bounds and marks it not ready on zero bounds or unmount. The command handler checks the current state first and otherwise waits for the matching transition before calling `activate`; this covers both an already-mounted pane and a newly mounted pane without losing an event. IPC message ordering ensures the bounds update reaches the main process first. A two-second timeout returns an error but leaves the mounted pane lifecycle unchanged so a later command can succeed.

If the window is too narrow to mount the split, `op=open` and bookmark activation fall back to the existing full-width web tab instead of waiting. This fallback may change `activeId` from the session to that web tab, but it preserves the current `/s/...` or `/chat` route so returning to the session is deterministic. `op=active` reports no split target while the stored split has zero bounds, is covered by an overlay, or is otherwise not visible.

This preserves the existing Playwright/CDP data path: model clicks, typing, scrolling, and screenshots operate on the same visible page the user sees.

## Bookmark Manager

Keep the star button in the web toolbar and the compact bookmark shortcuts on the new-tab page. Add a `Bookmarks` destination to the right sidebar icon rail so saved pages are available from every chat session.

The bookmark panel provides:

- a search field filtering title and URL;
- a list showing title and URL;
- row activation that opens the bookmark in split view while a session is active, and otherwise opens the normal web tab;
- an explicit full-tab action;
- inline title renaming;
- deletion;
- empty and no-search-result states.

Renaming trims the title, preserves list order, and falls back to the URL when the title is empty. Bookmark URLs are not edited in place; deleting and bookmarking the intended page uses the existing normalized browser URL and avoids a second URL-validation path. Bookmark storage remains local to the desktop profile in `openprogram.bookmarks`; all changes continue emitting `openprogram:bookmarks-changed`, so the toolbar star, new-tab shortcuts, and manager update immediately.

No bookmark folders, remote synchronization, favicon fetching, import, or export are added. Those features are not required for finding, opening, editing, and removing saved pages.

## Error Handling

- A failed local-storage write leaves the last readable bookmark list visible.
- A stale split id is cleared during restore and when tabs close.
- If Electron cannot activate the requested native view, the split path reports failure without changing the chat. A failed narrow-window full-tab fallback restores the previous session `activeId` before reporting failure.
- When the desktop bridge is unavailable, existing single-pane iframe behavior remains unchanged.

## Validation

Automated checks must cover:

- pinning a web tab without changing the active session;
- ratio clamping and persistence migration;
- closing and restoring a pinned web tab;
- `webtab.command(open|active)` selecting the visible split target;
- bookmark add, remove, rename, search wiring, and change events;
- the bookmark manager and split actions being reachable from their visible UI controls.

Live Electron verification must confirm:

- chat messages and composer remain usable while a real page is visible on the right;
- dragging the divider updates the native page bounds without reloading it;
- switching chat sessions preserves the right page state;
- while split view is visible, a model browser action targets the right page without changing the chat tab;
- bookmarks can be added, found from the right rail, searched, edited, opened in split and full-tab modes, and deleted;
- attachment previews, Radix dialogs, and branch merge overlays are not covered by the native page;
- narrow-window fallback and reopening the split preserve the pinned tab and ratio.
