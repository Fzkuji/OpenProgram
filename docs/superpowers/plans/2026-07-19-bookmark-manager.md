# Bookmark Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make saved web bookmarks discoverable and manageable from the persistent right sidebar, with immediate synchronization and split/full-tab opening.

**Architecture:** Keep `openprogram.bookmarks` local storage and its existing change event as the sole data source. Add one title-renaming operation, one focused `BookmarksPanel`, and one right-dock view. The panel uses the split store interface from the desktop-web-split plan instead of creating its own navigation state.

**Tech Stack:** TypeScript, React 18, Zustand 5, Lucide icons, CSS, Node assertion checks.

## Global Constraints

- Preserve the toolbar star and new-tab bookmark shortcuts.
- Add search across title and URL, inline title rename, split open, full-tab open, delete, empty state, and no-results state.
- Renaming trims the title, preserves order and URL, falls back to URL for an empty title, and emits `openprogram:bookmarks-changed` only after successful storage.
- Do not add folders, drag sorting, server synchronization, favicon fetching, import/export, a backend schema, or a dependency.
- When a session and split layout are available, normal bookmark activation opens beside chat and collapses the right panel; explicit full-tab activation always uses `openWebTab`.

---

### Task 1: Bookmark rename operation

**Files:**
- Modify: `web/lib/bookmarks.ts`
- Modify: `web/scripts/check-bookmarks.mjs`

**Interfaces:**
- Produces: `renameBookmark(url: string, title: string): Bookmark[]`.

- [ ] **Step 1: Write failing data checks**

Extend `check-bookmarks.mjs`:

```js
assert.deepEqual(
  bookmarks.renameBookmark(second.url, "  Renamed  "),
  [{ title: "Renamed", url: second.url }],
);
assert.deepEqual(
  bookmarks.renameBookmark(second.url, "   "),
  [{ title: second.url, url: second.url }],
);
assert.deepEqual(
  bookmarks.renameBookmark("https://missing.example/", "Missing"),
  [{ title: second.url, url: second.url }],
);
```

Also assert exactly one change event per successful rename and none for a missing URL or failed storage write.

- [ ] **Step 2: Run and verify RED**

Run: `cd web && npm run check:bookmarks`

Expected: failure because `renameBookmark` is undefined.

- [ ] **Step 3: Implement minimal rename**

```ts
export function renameBookmark(url: string, title: string): Bookmark[] {
  const bookmarks = readBookmarks();
  const index = bookmarks.findIndex((bookmark) => bookmark.url === url);
  if (index < 0) return bookmarks;
  const next = bookmarks.map((bookmark, i) =>
    i === index ? { ...bookmark, title: title.trim() || bookmark.url } : bookmark,
  );
  return saveBookmarks(next);
}
```

- [ ] **Step 4: Run the bookmark check**

Run: `cd web && npm run check:bookmarks`

Expected: `bookmark storage checks passed`.

- [ ] **Step 5: Commit**

```bash
git add web/lib/bookmarks.ts web/scripts/check-bookmarks.mjs
git commit -m "feat(bookmarks): support title renaming"
```

### Task 2: Persistent right-sidebar manager

**Files:**
- Create: `web/components/right-sidebar/bookmarks-panel.tsx`
- Modify: `web/components/right-sidebar/right-sidebar.tsx`
- Modify: `web/app/styles/right-dock.css`
- Modify: `web/scripts/check-bookmarks.mjs`

**Interfaces:**
- Consumes: `readBookmarks`, `renameBookmark`, `removeBookmark`, `BOOKMARKS_CHANGE_EVENT`, `openWebTab`, `openWebTabInSplit`, `isDesktopSplitLayoutAvailable`.
- Produces: right-dock view id `bookmarks`.

- [ ] **Step 1: Add failing UI structure checks**

Require `bookmarks-panel.tsx` to subscribe to `BOOKMARKS_CHANGE_EVENT`, filter with both `bookmark.title.toLowerCase()` and `bookmark.url.toLowerCase()`, call rename/remove functions, expose `Open in full tab`, and render distinct empty/no-results text. Require `right-sidebar.tsx` and `right-dock.css` to register `data-view="bookmarks"`.

- [ ] **Step 2: Run and verify RED**

Run: `cd web && npm run check:bookmarks`

Expected: failure because the manager file/view is absent.

- [ ] **Step 3: Implement the panel and right-dock entry**

Create a focused client component with `bookmarks`, `query`, `editingUrl`, and `draftTitle` state. Refresh on the change event. Row title click calls split open only when the active center tab is a session, the desktop bridge exists, and split layout is available; otherwise it calls `openWebTab`. The explicit full-tab button always calls `openWebTab`. A successful split open calls `setRightDockOpen(false)`.

```tsx
const [bookmarks, setBookmarks] = useState<Bookmark[]>(readBookmarks);
const [query, setQuery] = useState("");
const [editingUrl, setEditingUrl] = useState<string | null>(null);
const [draftTitle, setDraftTitle] = useState("");
useEffect(() => {
  const refresh = () => setBookmarks(readBookmarks());
  window.addEventListener(BOOKMARKS_CHANGE_EVENT, refresh);
  return () => window.removeEventListener(BOOKMARKS_CHANGE_EVENT, refresh);
}, []);
const needle = query.trim().toLowerCase();
const filtered = bookmarks.filter((bookmark) =>
  !needle || bookmark.title.toLowerCase().includes(needle)
    || bookmark.url.toLowerCase().includes(needle),
);
```

```ts
function openBesideChat(url: string) {
  const tabs = useCenterTabs.getState();
  const active = tabs.tabs.find((tab) => tab.id === tabs.activeId);
  if (desktopBridge() && active?.kind === "session"
      && isDesktopSplitLayoutAvailable()) {
    tabs.openWebTabInSplit(url);
    useSessionStore.getState().setRightDockOpen(false);
    return;
  }
  tabs.openWebTab(url);
}

function saveRename(url: string) {
  setBookmarks(renameBookmark(url, draftTitle));
  setEditingUrl(null);
}
```

```tsx
{bookmarks.length === 0 ? (
  <div className="bookmarks-empty">{text("No bookmarks yet", "还没有书签")}</div>
) : filtered.length === 0 ? (
  <div className="bookmarks-empty">{text("No matching bookmarks", "没有匹配的书签")}</div>
) : filtered.map((bookmark) => (
  <div className="bookmark-row" key={bookmark.url}>
    <button onClick={() => openBesideChat(bookmark.url)}>{bookmark.title}</button>
    <span>{bookmark.url}</span>
    <button onClick={() => useCenterTabs.getState().openWebTab(bookmark.url)}
      aria-label={text("Open in full tab", "在完整标签页中打开")} />
    <button onClick={() => removeBookmark(bookmark.url)}
      aria-label={text("Delete bookmark", "删除书签")} />
  </div>
))}
```

Add a `Bookmark` Lucide nav icon and label beside History and Files. Add the exact view selector:

```css
.right-sidebar[data-view="bookmarks"] .right-view[data-view="bookmarks"] {
  display: flex;
}
```

Rows show title, URL, inline edit/save/cancel controls, full-tab action, and delete action. All icon-only buttons require localized `title` and `aria-label`.

- [ ] **Step 4: Run focused and aggregate web checks**

Run:

```bash
cd web
npm run check:bookmarks
npm run check:web-split
npm run check
npm run build
```

Expected: all exit `0`; build reports successful compilation.

- [ ] **Step 5: Commit**

```bash
git add web/components/right-sidebar/bookmarks-panel.tsx web/components/right-sidebar/right-sidebar.tsx web/app/styles/right-dock.css web/scripts/check-bookmarks.mjs
git commit -m "feat(bookmarks): add right-sidebar manager"
```

### Task 3: Live desktop acceptance

**Files:**
- Modify only if a live failure requires a focused fix and matching regression assertion.

**Interfaces:**
- Verifies the completed split and bookmark plans together.

- [ ] **Step 1: Start the development desktop app**

Use the repository's existing development launcher and CDP port. Confirm the renderer loads the current working tree, not the previously packaged app.

- [ ] **Step 2: Verify split behavior with a real page**

Open a session, open `https://example.com/`, enter split mode, drag the divider, switch sessions, select the web tab full-width, and return to chat. Confirm the page does not reload and the split ratio restores.

- [ ] **Step 3: Verify model control and occlusion**

Use the existing app browser tool with `engine=app` to obtain the right-page target and perform one visible action. Open an attachment preview or Radix dialog and confirm the native page receives zero bounds and does not cover the overlay.

- [ ] **Step 4: Verify the bookmark workflow**

Add a bookmark from the web toolbar, open Bookmarks from the right rail, search by title and URL, rename it, open it in split and full-tab modes, delete it, and reload the app. Confirm every surface updates immediately and deletion persists.

- [ ] **Step 5: Run final automated verification and commit any focused fixes**

Run:

```bash
cd web && npm run check && npm run build
cd .. && node desktop/scripts/check-webtab-navigation.js
python -m pytest tests/unit/test_webtab_control.py -q
git diff --check
```

Expected: every command exits `0`. If live verification required code changes, first add a failing assertion to the nearest existing check, implement the minimal fix, rerun the covering check, and commit with a scope-specific message.
