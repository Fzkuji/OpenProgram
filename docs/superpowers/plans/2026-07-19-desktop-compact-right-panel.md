# Desktop Compact Right Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current expanding right sidebar with a permanent 49 DIP icon rail and a separate rounded, resizable content panel on its left while preserving and completing the existing bookmark-management workflow.

**Architecture:** Keep `RightSidebar` as the single flex item mounted by `AppShell`, but render it as a horizontal shell containing an optional layout-occupying panel and a permanent icon-only rail. Keep `useSessionStore.rightDock` as the open/view source of truth, retain the existing `data-view` view host, and use the browser-native `storage` event alongside the existing same-window bookmark event for cross-window updates. No new UI dependency, drag-and-drop dependency, persistence layer, or test dependency is introduced.

**Tech Stack:** TypeScript, React 18, Zustand 5, Lucide icons, CSS, Electron 37 `WebContentsView`, Node `assert` checks.

## Global Constraints

- The right rail is always visible, icon-only, and exactly 49 DIP wide.
- The content panel is immediately to the rail's left, defaults to 320 DIP, clamps to 280–560 DIP, has 8 DIP outer gaps, a 16 DIP radius, the existing popover border/shadow tokens, and a 40 DIP header.
- The panel is non-modal and always occupies flex layout space, including below 900px, so a native `WebContentsView` cannot cover it.
- History, Bookmarks, and Files remain 32 DIP controls with tooltips and the existing neutral selected background. Do not add a persistent accent line or copy the Codex screenshot's dotted execution timeline into non-timeline views.
- Clicking the selected rail item or pressing `Escape` closes the panel and returns focus to that rail item; clicking a different rail item opens or switches the panel.
- History, Bookmarks, and Files are native `button` elements. The resize handle is a keyboard-operable vertical separator with current/minimum/maximum ARIA values.
- Preserve bookmark add/remove from the web toolbar, new-tab shortcuts, title/URL search, inline rename, delete, split-open, full-tab-open, empty/no-result states, immediate UI updates, and profile-local persistence.
- Bookmark view selection must survive reload, and bookmark mutations must update every OpenProgram window without reload.
- Do not add bookmark folders, cloud synchronization, favicon fetching, import/export, a backend bookmark schema, or a new dependency.
- Perform live testing against the development application on port 18200; leave the stable 18100 application unchanged.

---

### Task 1: Persist the Bookmarks view and synchronize bookmark consumers across windows

**Files:**
- Modify: `web/lib/bookmarks.ts:6-55`
- Modify: `web/lib/session-store/index.ts:267-305`
- Modify: `web/components/center-tabs/web-tab-pane.tsx:37-77`
- Modify: `web/components/center-tabs/new-tab-page.tsx:21-46`
- Modify: `web/components/right-sidebar/bookmarks-panel.tsx:6-30`
- Modify: `web/scripts/check-bookmarks.mjs:5-253`

**Interfaces:**
- Consumes: `BOOKMARKS_STORAGE_KEY`, `BOOKMARKS_CHANGE_EVENT`, and browser `storage` events.
- Produces: `subscribeBookmarks(listener: () => void): () => void` and a persisted right-dock view id of `bookmarks`.

- [ ] **Step 1: Add failing persistence and subscription checks**

Add the session-store source to `check-bookmarks.mjs` and require `bookmarks` in the persisted view whitelist:

```js
const sessionStorePath = new URL("../lib/session-store/index.ts", import.meta.url);
const sessionStore = readFileSync(sessionStorePath, "utf8");

assert.match(
  sessionStore,
  /const VALID_VIEWS = new Set\(\[[^\]]*"bookmarks"[^\]]*\]\);/s,
  "Bookmarks must survive right-dock restore",
);
```

After the existing bookmark storage assertions, exercise same-window delivery, cross-window-style `storage` delivery, irrelevant keys, and cleanup:

```js
let refreshes = 0;
const unsubscribe = bookmarks.subscribeBookmarks(() => refreshes++);

windowEvents.dispatchEvent(new Event(bookmarks.BOOKMARKS_CHANGE_EVENT));
assert.equal(refreshes, 1, "same-window bookmark event must refresh subscribers");

const relevantStorage = new Event("storage");
Object.defineProperty(relevantStorage, "key", {
  configurable: true,
  value: bookmarks.BOOKMARKS_STORAGE_KEY,
});
windowEvents.dispatchEvent(relevantStorage);
assert.equal(refreshes, 2, "shared-profile storage changes must refresh subscribers");

const unrelatedStorage = new Event("storage");
Object.defineProperty(unrelatedStorage, "key", {
  configurable: true,
  value: "unrelated.key",
});
windowEvents.dispatchEvent(unrelatedStorage);
assert.equal(refreshes, 2, "unrelated storage writes must be ignored");

unsubscribe();
windowEvents.dispatchEvent(new Event(bookmarks.BOOKMARKS_CHANGE_EVENT));
windowEvents.dispatchEvent(relevantStorage);
assert.equal(refreshes, 2, "unsubscribe must remove both listeners");
```

Replace the existing component-source assertions for direct custom-event listeners with assertions that all three consumers call the shared subscription:

```js
for (const [name, text] of [
  ["web-tab-pane.tsx", webTab],
  ["new-tab-page.tsx", newTab],
  ["bookmarks-panel.tsx", manager],
]) {
  assert.match(text, /subscribeBookmarks\(refresh\)/, `${name} must use shared bookmark subscription`);
}
```

- [ ] **Step 2: Run the focused check and verify RED**

Run:

```bash
cd web
npm run check:bookmarks
```

Expected: FAIL because `subscribeBookmarks` is not exported and `VALID_VIEWS` does not contain `bookmarks`.

- [ ] **Step 3: Implement the shared subscription and persistence fix**

Add this function to `web/lib/bookmarks.ts` immediately after the event constants:

```ts
export function subscribeBookmarks(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};

  const onStorage = (event: StorageEvent) => {
    if (event.key === BOOKMARKS_STORAGE_KEY) listener();
  };

  window.addEventListener(BOOKMARKS_CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(BOOKMARKS_CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}
```

Add `bookmarks` to the right-dock persistence whitelist in `web/lib/session-store/index.ts`:

```ts
const VALID_VIEWS = new Set(["history", "context", "detail", "files", "bookmarks"]);
```

In `web-tab-pane.tsx`, `new-tab-page.tsx`, and `bookmarks-panel.tsx`, import `subscribeBookmarks` from `@/lib/bookmarks` and replace each direct event-listener effect with:

```ts
useEffect(() => {
  const refresh = () => setBookmarks(readBookmarks());
  refresh();
  return subscribeBookmarks(refresh);
}, []);
```

For `BookmarkButton`, retain the URL dependency and its boolean update:

```ts
useEffect(() => {
  const refresh = () => setBookmarked(isBookmarked(url));
  refresh();
  return subscribeBookmarks(refresh);
}, [url]);
```

Remove the now-unused `BOOKMARKS_CHANGE_EVENT` imports from those three components. Do not change bookmark serialization, ordering, rename behavior, navigation behavior, or storage keys.

- [ ] **Step 4: Run focused and aggregate checks**

Run:

```bash
cd web
npm run check:bookmarks
npm run check
```

Expected: both commands exit `0`; the focused command prints `bookmark storage checks passed`.

- [ ] **Step 5: Commit**

```bash
git add web/lib/bookmarks.ts web/lib/session-store/index.ts \
  web/components/center-tabs/web-tab-pane.tsx \
  web/components/center-tabs/new-tab-page.tsx \
  web/components/right-sidebar/bookmarks-panel.tsx \
  web/scripts/check-bookmarks.mjs
git commit -m "fix(bookmarks): sync manager across windows"
```

### Task 2: Build the permanent rail and accessible panel interactions

**Files:**
- Create: `web/lib/right-panel-behavior.ts`
- Modify: `web/components/right-sidebar/right-sidebar.tsx:29-387`
- Modify: `web/scripts/check-bookmarks.mjs:5-170`
- Modify: `web/package.json:5-14`

**Interfaces:**
- Consumes: `useSessionStore.rightDock`, `setRightDockOpen(open: boolean)`, `setRightDockView(view: string)`, existing `rightDock` legacy shims, `sidebarNavItemClass`, and existing view components.
- Produces: `resolveRightPanelAction`, `panelWidthAfterKey`, `clampPanelWidth`, and the shared `handlePanelResizeKey` event handler as directly executable interaction rules; the semantic `.right-sidebar-rail` / `.right-sidebar-panel` shell; native rail buttons; close/focus behavior; and a keyboard-operable `.right-panel-resize` separator.

- [ ] **Step 1: Add failing executable interaction checks**

Change `check:bookmarks` so Node can import the small TypeScript behavior module directly:

```json
"check:bookmarks": "node --no-warnings --experimental-strip-types scripts/check-bookmarks.mjs"
```

Import the not-yet-created module at the top of `check-bookmarks.mjs`:

```js
import {
  RIGHT_PANEL_DEFAULT,
  RIGHT_PANEL_GAP,
  RIGHT_PANEL_MAX,
  RIGHT_PANEL_MIN,
  RIGHT_RAIL_WIDTH,
  clampPanelWidth,
  handlePanelResizeKey,
  panelWidthAfterKey,
  resolveRightPanelAction,
} from "../lib/right-panel-behavior.ts";
```

Add these direct behavior assertions. They execute the same pure rules the React handlers will call; they do not infer behavior from source text:

```js
assert.equal(RIGHT_PANEL_DEFAULT, 320);
assert.equal(RIGHT_PANEL_MIN, 280);
assert.equal(RIGHT_PANEL_MAX, 560);
assert.equal(RIGHT_PANEL_GAP, 8);
assert.equal(RIGHT_RAIL_WIDTH, 49);

assert.equal(clampPanelWidth(120), 280);
assert.equal(clampPanelWidth(420), 420);
assert.equal(clampPanelWidth(900), 560);

assert.equal(panelWidthAfterKey(320, "ArrowLeft"), 336);
assert.equal(panelWidthAfterKey(320, "ArrowRight"), 304);
assert.equal(panelWidthAfterKey(280, "ArrowRight"), 280);
assert.equal(panelWidthAfterKey(560, "ArrowLeft"), 560);
assert.equal(panelWidthAfterKey(400, "Home"), 280);
assert.equal(panelWidthAfterKey(400, "End"), 560);
assert.equal(panelWidthAfterKey(400, "Enter"), null);

assert.deepEqual(
  resolveRightPanelAction(
    { open: true, view: "bookmarks" },
    { type: "select", view: "bookmarks" },
  ),
  { open: false, view: "bookmarks", focusView: "bookmarks" },
  "re-clicking the selected rail item must close and target that item for focus",
);
assert.deepEqual(
  resolveRightPanelAction(
    { open: true, view: "bookmarks" },
    { type: "escape" },
  ),
  { open: false, view: "bookmarks", focusView: "bookmarks" },
  "Escape must close and target the selected rail item for focus",
);
assert.deepEqual(
  resolveRightPanelAction(
    { open: true, view: "bookmarks" },
    { type: "select", view: "files" },
  ),
  { open: true, view: "files", focusView: null },
  "selecting another rail item must switch the open panel",
);
assert.deepEqual(
  resolveRightPanelAction(
    { open: false, view: "bookmarks" },
    { type: "escape" },
  ),
  { open: false, view: "bookmarks", focusView: null },
  "Escape on a closed panel must not move focus",
);
```

Add a dependency-free EventTarget/attribute harness that binds and executes the same `handlePanelResizeKey` function used by the separator's React `onKeyDown` handler:

```js
class SeparatorHarness extends EventTarget {
  #attributes = new Map();

  setAttribute(name, value) {
    this.#attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.#attributes.get(name) ?? null;
  }
}

const separator = new SeparatorHarness();
separator.setAttribute("role", "separator");
separator.setAttribute("aria-valuemin", RIGHT_PANEL_MIN);
separator.setAttribute("aria-valuemax", RIGHT_PANEL_MAX);
let renderedPanelWidth = RIGHT_PANEL_DEFAULT;

function renderSeparator(nextWidth) {
  renderedPanelWidth = nextWidth;
  separator.setAttribute("aria-valuenow", nextWidth);
}

function dispatchSeparatorKey(key) {
  const event = new Event("keydown", { cancelable: true });
  Object.defineProperty(event, "key", { value: key });
  separator.dispatchEvent(event);
  return event;
}

separator.addEventListener("keydown", (event) => {
  handlePanelResizeKey(event, renderedPanelWidth, renderSeparator);
});
renderSeparator(RIGHT_PANEL_DEFAULT);

assert.equal(separator.getAttribute("aria-valuemin"), "280");
assert.equal(separator.getAttribute("aria-valuemax"), "560");
assert.equal(separator.getAttribute("aria-valuenow"), "320");
assert.equal(dispatchSeparatorKey("ArrowLeft").defaultPrevented, true);
assert.equal(separator.getAttribute("aria-valuenow"), "336");
dispatchSeparatorKey("Home");
assert.equal(separator.getAttribute("aria-valuenow"), "280");
dispatchSeparatorKey("ArrowRight");
assert.equal(separator.getAttribute("aria-valuenow"), "280");
dispatchSeparatorKey("End");
assert.equal(separator.getAttribute("aria-valuenow"), "560");
dispatchSeparatorKey("ArrowLeft");
assert.equal(separator.getAttribute("aria-valuenow"), "560");
assert.equal(dispatchSeparatorKey("Enter").defaultPrevented, false);
assert.equal(separator.getAttribute("aria-valuenow"), "560");
```

After the existing `parseTsx` and `attr` helpers, add one AST wiring check that locates the actual separator and verifies its `onKeyDown` arrow invokes the exported handler exercised above:

```js
const rightSidebarFile = parseTsx(rightSidebar, "right-sidebar.tsx");
let resizeSeparator;
function findResizeSeparator(node) {
  if (
    ts.isJsxSelfClosingElement(node) &&
    attr(node, "role")?.initializer?.text === "separator"
  ) {
    resizeSeparator = node;
    return;
  }
  ts.forEachChild(node, findResizeSeparator);
}
findResizeSeparator(rightSidebarFile);
assert.ok(resizeSeparator, "keyboard resize separator missing");
const resizeKeyDown = attr(resizeSeparator, "onKeyDown");
assert.ok(resizeKeyDown, "separator onKeyDown missing");
assert.ok(ts.isJsxExpression(resizeKeyDown.initializer));
const resizeKeyExpression = resizeKeyDown.initializer.expression;
assert.ok(resizeKeyExpression && ts.isArrowFunction(resizeKeyExpression));
assert.ok(ts.isCallExpression(resizeKeyExpression.body));
assert.equal(
  resizeKeyExpression.body.expression.getText(rightSidebarFile),
  "handlePanelResizeKey",
  "separator must invoke the handler covered by the EventTarget harness",
);
```

Retain only small semantic/AST checks for the three native rail buttons, the separator ARIA attributes, no nested buttons, localized icon labels, bookmark focus-visible styles, and the bookmark navigation matrix. Do not use source regex to claim resize, selected-item close, Escape, or focus-target behavior; those behaviors are covered by the direct assertions above and later by live `document.activeElement` checks.

- [ ] **Step 2: Run the focused check and verify RED**

Run:

```bash
cd web
npm run check:bookmarks
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` because `right-panel-behavior.ts` does not exist yet.

- [ ] **Step 3: Implement the shared behavior module and call its handler from React**

Create `web/lib/right-panel-behavior.ts`:

```ts
export const RIGHT_RAIL_WIDTH = 49;
export const RIGHT_PANEL_DEFAULT = 320;
export const RIGHT_PANEL_MIN = 280;
export const RIGHT_PANEL_MAX = 560;
export const RIGHT_PANEL_GAP = 8;
export const RIGHT_PANEL_KEY_STEP = 16;

export type RightPanelState = { open: boolean; view: string };
export type RightPanelAction =
  | { type: "select"; view: string }
  | { type: "escape" };

export function clampPanelWidth(width: number): number {
  return Math.max(RIGHT_PANEL_MIN, Math.min(RIGHT_PANEL_MAX, width));
}

export function panelWidthAfterKey(width: number, key: string): number | null {
  if (key === "ArrowLeft") return clampPanelWidth(width + RIGHT_PANEL_KEY_STEP);
  if (key === "ArrowRight") return clampPanelWidth(width - RIGHT_PANEL_KEY_STEP);
  if (key === "Home") return RIGHT_PANEL_MIN;
  if (key === "End") return RIGHT_PANEL_MAX;
  return null;
}

type ResizeKeyEvent = {
  key: string;
  preventDefault: () => void;
};

export function handlePanelResizeKey(
  event: ResizeKeyEvent,
  width: number,
  commit: (nextWidth: number) => void,
): boolean {
  const next = panelWidthAfterKey(width, event.key);
  if (next === null) return false;
  event.preventDefault();
  commit(next);
  return true;
}

export function resolveRightPanelAction(
  state: RightPanelState,
  action: RightPanelAction,
): RightPanelState & { focusView: string | null } {
  if (action.type === "escape") {
    return state.open
      ? { ...state, open: false, focusView: state.view }
      : { ...state, focusView: null };
  }
  if (state.open && state.view === action.view) {
    return { ...state, open: false, focusView: action.view };
  }
  return { open: true, view: action.view, focusView: null };
}
```

In `right-sidebar.tsx`, change the Lucide import and replace the existing sidebar width constants/state/handlers with:

```tsx
import { Bookmark, X } from "lucide-react";
import {
  RIGHT_PANEL_DEFAULT,
  RIGHT_PANEL_GAP,
  RIGHT_PANEL_MAX,
  RIGHT_PANEL_MIN,
  RIGHT_RAIL_WIDTH,
  clampPanelWidth,
  handlePanelResizeKey,
  resolveRightPanelAction,
} from "@/lib/right-panel-behavior";

// Inside RightSidebar:
const [panelWidth, setPanelWidth] = useState(RIGHT_PANEL_DEFAULT);
const [resizing, setResizing] = useState(false);
const dragRef = useRef<{ startX: number; startW: number } | null>(null);
const toggleButtonRef = useRef<HTMLButtonElement>(null);
const railButtonRefs = useRef<Record<string, HTMLButtonElement | null>>({});

function restoreRailFocus(targetView = view) {
  const target = railButtonRefs.current[targetView] ?? toggleButtonRef.current;
  requestAnimationFrame(() => target?.focus());
}

function closePanelAndRestoreFocus(targetView = view) {
  setRightDockOpen(false);
  restoreRailFocus(targetView);
}

function onToggleRail() {
  if (open) closePanelAndRestoreFocus();
  else setRightDockOpen(true);
}

function onNavClick(v: string, button: HTMLButtonElement) {
  const next = resolveRightPanelAction(
    { open, view },
    { type: "select", view: v },
  );
  if (next.view !== view) setRightDockView(next.view);
  setRightDockOpen(next.open);
  if (next.focusView) requestAnimationFrame(() => button.focus());
}

function onSidebarKeyDown(event: React.KeyboardEvent<HTMLElement>) {
  if (event.key !== "Escape" || !open) return;
  event.preventDefault();
  event.stopPropagation();
  const next = resolveRightPanelAction({ open, view }, { type: "escape" });
  setRightDockOpen(next.open);
  if (next.focusView) restoreRailFocus(next.focusView);
}

function onResizePointerDown(event: React.PointerEvent<HTMLDivElement>) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.currentTarget.setPointerCapture(event.pointerId);
  dragRef.current = { startX: event.clientX, startW: panelWidth };
  setResizing(true);
}

function onResizePointerMove(event: React.PointerEvent<HTMLDivElement>) {
  if (!dragRef.current || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
  setPanelWidth(clampPanelWidth(dragRef.current.startW + dragRef.current.startX - event.clientX));
}

function finishResize(event: React.PointerEvent<HTMLDivElement>) {
  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
  dragRef.current = null;
  setResizing(false);
}

```

Delete `onResizeMouseDown` and its document-level listeners. Keep the existing `window.rightDock` compatibility effect unchanged.

- [ ] **Step 4: Replace the single-column sidebar shell**

Compute the header text before `return`:

```tsx
const panelTitle =
  view === VIEW_HISTORY ? t("right.history")
  : view === VIEW_BOOKMARKS ? text("Bookmarks", "书签")
  : view === VIEW_FILES ? text("Files", "文件")
  : view === VIEW_CONTEXT ? t("right.context")
  : text("Execution detail", "执行详情");

const shellWidth = open
  ? panelWidth + RIGHT_RAIL_WIDTH + RIGHT_PANEL_GAP * 2
  : RIGHT_RAIL_WIDTH;
```

Replace the current `<aside>` return block with this structure. The existing `HistoryGraphPanel`, `DetailPanel`, `ContextCommitTimeline`, and `FileTree` children remain the same components and IDs:

```tsx
<aside
  id="rightSidebar"
  className={`sidebar right-sidebar${open ? "" : " collapsed"}`}
  style={{ width: `${shellWidth}px`, minWidth: `${shellWidth}px` }}
  data-view={view}
  data-resizing={resizing ? "true" : "false"}
  onKeyDown={onSidebarKeyDown}
>
  <section
    className="right-sidebar-panel"
    style={{ width: `${panelWidth}px` }}
    role="region"
    aria-labelledby="right-panel-title"
    hidden={!open}
  >
      <div
        className="right-panel-resize"
        role="separator"
        aria-label={t("right.resize_panel")}
        aria-orientation="vertical"
        aria-valuemin={RIGHT_PANEL_MIN}
        aria-valuemax={RIGHT_PANEL_MAX}
        aria-valuenow={panelWidth}
        tabIndex={0}
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={finishResize}
        onPointerCancel={finishResize}
        onKeyDown={(event) =>
          handlePanelResizeKey(event, panelWidth, setPanelWidth)
        }
      />
      <header className="right-sidebar-panel-header">
        <span id="right-panel-title">{panelTitle}</span>
        <button
          type="button"
          className={sidebarToggleClass}
          onClick={() => closePanelAndRestoreFocus()}
          title={text("Close panel", "关闭面板")}
          aria-label={text("Close panel", "关闭面板")}
        >
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <div className="right-view-host">
        <div className="right-view" data-view={VIEW_BOOKMARKS}>
          <BookmarksPanel />
        </div>
        <div className="right-view" data-view={VIEW_FILES}>
          {treeProjectId ? (
            <FileTree projectId={treeProjectId} />
          ) : (
            <div style={{ padding: 16, fontSize: 13, color: "var(--text-dim)" }}>
              {text("Bind a project to browse files", "绑定项目后可浏览文件")}
            </div>
          )}
        </div>
        <div id="historyPanel" className="right-view" data-view={VIEW_HISTORY}>
          <HistoryGraphPanel />
        </div>
        <div id="detailPanel" className="right-view" data-view={VIEW_DETAIL}>
          <DetailPanel />
        </div>
        <div id="commitsPanel" className="right-view" data-view={VIEW_CONTEXT}>
          <ContextCommitTimeline />
        </div>
      </div>
  </section>

  <nav className="right-sidebar-rail" aria-label={text("Workspace tools", "工作区工具")}>
    <div className="right-sidebar-rail-header">
      <button
        ref={toggleButtonRef}
        className={sidebarToggleClass}
        onClick={onToggleRail}
        onMouseEnter={() => toggleIconRef.current?.startAnimation?.()}
        onMouseLeave={() => toggleIconRef.current?.stopAnimation?.()}
        title={t("right.toggle_panel")}
        aria-label={t("right.toggle_panel")}
        aria-expanded={open}
        type="button"
      >
        {open ? (
          <PanelLeftCloseIcon ref={toggleIconRef} size={20} style={{ transform: "scaleX(-1)" }} />
        ) : (
          <PanelLeftOpenIcon ref={toggleIconRef} size={20} style={{ transform: "scaleX(-1)" }} />
        )}
      </button>
    </div>

    <div className="right-sidebar-rail-items">
      <button
        ref={(node) => { railButtonRefs.current[VIEW_HISTORY] = node; }}
        type="button"
        className={sidebarNavItemClass + " right-nav-item" +
          (view === VIEW_HISTORY ? " " + sidebarNavItemActiveClass : "")}
        data-view={VIEW_HISTORY}
        onClick={(event) => onNavClick(VIEW_HISTORY, event.currentTarget)}
        onMouseEnter={() => historyIconRef.current?.startAnimation?.()}
        onMouseLeave={() => historyIconRef.current?.stopAnimation?.()}
        title={t("right.history")}
        aria-label={t("right.history")}
        aria-pressed={open && view === VIEW_HISTORY}
      >
        <span className={sidebarNavIconClass}>
          <GitGraphIcon ref={historyIconRef} size={20} />
        </span>
      </button>

      <button
        ref={(node) => { railButtonRefs.current[VIEW_BOOKMARKS] = node; }}
        type="button"
        className={sidebarNavItemClass + " right-nav-item" +
          (view === VIEW_BOOKMARKS ? " " + sidebarNavItemActiveClass : "")}
        data-view={VIEW_BOOKMARKS}
        onClick={(event) => onNavClick(VIEW_BOOKMARKS, event.currentTarget)}
        title={text("Bookmarks", "书签")}
        aria-label={text("Bookmarks", "书签")}
        aria-pressed={open && view === VIEW_BOOKMARKS}
      >
        <span className={sidebarNavIconClass}>
          <Bookmark size={20} aria-hidden="true" />
        </span>
      </button>

      <button
        ref={(node) => { railButtonRefs.current[VIEW_FILES] = node; }}
        type="button"
        className={sidebarNavItemClass + " right-nav-item" +
          (view === VIEW_FILES ? " " + sidebarNavItemActiveClass : "")}
        data-view={VIEW_FILES}
        onClick={(event) => onNavClick(VIEW_FILES, event.currentTarget)}
        onMouseEnter={() => filesIconRef.current?.startAnimation?.()}
        onMouseLeave={() => filesIconRef.current?.stopAnimation?.()}
        title={text("Project files", "项目文件")}
        aria-label={text("Project files", "项目文件")}
        aria-pressed={open && view === VIEW_FILES}
      >
        <span className={sidebarNavIconClass}>
          <FolderOpenIcon ref={filesIconRef} size={20} />
        </span>
      </button>
    </div>
  </nav>
</aside>
```

Remove the unused `sidebarNavLabelClass` import. Do not expose the legacy Detail or Context views as permanent rail buttons; their existing `window.rightDock.show("detail" | "context")` entry points continue to select the content host.

- [ ] **Step 5: Run focused and type checks**

Run:

```bash
cd web
npm run check:bookmarks
npx tsc --noEmit
```

Expected: both commands exit `0`; the focused check prints `bookmark storage checks passed` and TypeScript reports no errors.

- [ ] **Step 6: Commit**

```bash
git add web/lib/right-panel-behavior.ts \
  web/components/right-sidebar/right-sidebar.tsx \
  web/scripts/check-bookmarks.mjs web/package.json
git commit -m "feat(desktop): add compact right panel controls"
```

### Task 3: Apply compact geometry and keep the panel in layout at narrow widths

**Files:**
- Modify: `web/app/styles/right-dock.css:1-169`
- Modify: `web/scripts/check-bookmarks.mjs:5-170`

**Interfaces:**
- Consumes: `.right-sidebar`, `.right-sidebar-panel`, `.right-sidebar-rail`, `.right-sidebar-panel-header`, `.right-panel-resize`, and `data-resizing` from Task 2.
- Produces: exact 49/320/280/560 panel geometry, 8 DIP gaps, a 16 DIP rounded surface, a 40 DIP header, and a non-fixed narrow layout.

- [ ] **Step 1: Add failing geometry and responsive checks**

Extend `check-bookmarks.mjs` with:

```js
for (const [selector, declaration] of [
  ["right-sidebar-rail", /width:\s*49px/],
  ["right-sidebar-panel", /margin:\s*8px/],
  ["right-sidebar-panel", /border-radius:\s*16px/],
  ["right-sidebar-panel", /box-shadow:\s*var\(--shadow-popover\)/],
  ["right-sidebar-panel-header", /height:\s*40px/],
]) {
  const rule = new RegExp(`\\.${selector}\\s*\\{[^}]*${declaration.source}`, "s");
  assert.match(rightDockCss, rule, `${selector} geometry missing`);
}

assert.match(rightDockCss, /\.right-sidebar-panel\[hidden\]\s*\{\s*display:\s*none;/);

assert.match(
  rightDockCss,
  /@media\s*\(max-width:\s*900px\)[\s\S]*\.right-sidebar[\s\S]*position:\s*relative;/,
  "narrow right panel must remain a layout participant",
);
```

- [ ] **Step 2: Run the focused check and verify RED**

Run:

```bash
cd web
npm run check:bookmarks
```

Expected: FAIL because the new compact-panel selectors and responsive override are absent.

- [ ] **Step 3: Add compact-panel CSS and remove narrow overlay behavior**

Replace the shell/view-host preamble in `right-dock.css` and retain the existing bookmark and other view-specific rules below it:

```css
.right-sidebar {
  position: relative;
  display: flex;
  flex: 0 0 auto;
  flex-direction: row;
  align-items: stretch;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-secondary);
  transition: width 150ms cubic-bezier(0.165, 0.84, 0.44, 1),
              min-width 150ms cubic-bezier(0.165, 0.84, 0.44, 1);
}
.right-sidebar[data-resizing="true"] { transition: none; }

.right-sidebar-panel {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 0 0 auto;
  flex-direction: column;
  box-sizing: border-box;
  margin: 8px;
  overflow: hidden;
  border: 1px solid var(--border-popover);
  border-radius: 16px;
  background: var(--bg-secondary);
  box-shadow: var(--shadow-popover);
}
.right-sidebar-panel[hidden] { display: none; }

.right-sidebar-panel-header {
  display: flex;
  height: 40px;
  flex: 0 0 40px;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
  padding: 4px 8px 4px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 550;
}

.right-sidebar-rail {
  display: flex;
  width: 49px;
  min-width: 49px;
  flex: 0 0 49px;
  flex-direction: column;
  box-sizing: border-box;
  border-left: 1px solid var(--border);
  background: var(--bg-secondary);
}

.right-sidebar-rail-header {
  display: flex;
  height: 40px;
  flex: 0 0 40px;
  align-items: center;
  justify-content: center;
}

.right-sidebar-rail-items {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 8px;
}

.right-sidebar-rail .right-nav-item {
  width: 32px;
  min-width: 32px;
  padding: 6px;
  justify-content: center;
}

.right-panel-resize {
  position: absolute;
  z-index: 10;
  top: 16px;
  bottom: 16px;
  left: 0;
  width: 8px;
  cursor: ew-resize;
  touch-action: none;
}
.right-panel-resize:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: -2px;
}

.right-view-host {
  position: relative;
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
.right-view {
  position: absolute;
  inset: 0;
  display: none;
  flex-direction: column;
}
.right-sidebar[data-view="history"] .right-view[data-view="history"],
.right-sidebar[data-view="detail"] .right-view[data-view="detail"],
.right-sidebar[data-view="context"] .right-view[data-view="context"],
.right-sidebar[data-view="files"] .right-view[data-view="files"],
.right-sidebar[data-view="bookmarks"] .right-view[data-view="bookmarks"] {
  display: flex;
}

@media (max-width: 900px) {
  .right-sidebar,
  .right-sidebar:not(.collapsed) {
    position: relative;
    inset: auto;
    z-index: auto;
    box-shadow: none;
  }
}
```

Delete the obsolete `.right-sidebar.collapsed .right-view-host` rule because the panel's `hidden` attribute now controls its closed state. Keep all bookmark rows and History/Detail/Context-specific styles unchanged.

- [ ] **Step 4: Run focused, aggregate, type, and production checks**

Run:

```bash
cd web
npm run check:bookmarks
npm run check
npx tsc --noEmit
npm run build
cd ../desktop
npm run check:webtabs
```

Expected: every command exits `0`; the focused check prints `bookmark storage checks passed`, TypeScript reports no errors, and Next reports a successful production build.

- [ ] **Step 5: Commit**

```bash
git add web/app/styles/right-dock.css web/scripts/check-bookmarks.mjs
git commit -m "style(desktop): add compact right panel geometry"
```

### Task 4: Verify geometry, accessibility, native-view bounds, and the full bookmark workflow

**Files:**
- Modify only if a live failure first receives a focused executable regression assertion in `web/scripts/check-bookmarks.mjs` or `web/lib/right-panel-behavior.ts`, followed by the smallest covering fix in an already-listed implementation file.

**Interfaces:**
- Verifies the completed compact-panel and bookmark interfaces without adding production APIs.

- [ ] **Step 1: Confirm or start the development worker on 18209/18200**

From the repository root, first inspect only the `dev` profile and probe both required services without changing any process:

```bash
OPENPROGRAM_PROFILE=dev \
OPENPROGRAM_BACKEND_PORT=18209 \
OPENPROGRAM_WEB_PORT=18200 \
openprogram worker status
```

```bash
curl -fsS http://127.0.0.1:18209/healthz
curl -fsS http://127.0.0.1:18200/chat >/dev/null
```

Also use the repository's identity probes from `openprogram/_ports.py`; they distinguish OpenProgram from an unrelated process occupying either port:

```bash
python - <<'PY'
from openprogram._ports import backend_is_ours, frontend_is_ours

assert backend_is_ours(18209) is True, "OpenProgram dev backend is not ready on 18209"
assert frontend_is_ours(18200) is True, "OpenProgram dev frontend is not ready on 18200"
PY
```

If status or either identity probe fails, start only the `dev` worker with the repository's supported `worker start` command:

```bash
OPENPROGRAM_PROFILE=dev \
OPENPROGRAM_BACKEND_PORT=18209 \
OPENPROGRAM_WEB_PORT=18200 \
openprogram worker start
```

Repeat both HTTP probes and the identity-probe block until all commands exit `0`:

```bash
curl -fsS http://127.0.0.1:18209/healthz
curl -fsS http://127.0.0.1:18200/chat >/dev/null
```

Expected: the OpenProgram backend reports its `/healthz` JSON on `18209`, the development frontend responds on `18200`, and both `backend_is_ours` / `frontend_is_ours` return `True`. This is the dev profile pairing documented in `docs/install/profiles.md`. Do not run a default-profile start, stop, or restart command, and do not change stable ports `18109` or `18100`.

- [ ] **Step 2: Launch Electron with an actual isolated user-data directory argument**

Create a disposable directory and pass Electron's supported command-line switch through `npm run`:

```bash
RIGHT_PANEL_PROFILE_DIR="$(mktemp -d /tmp/openprogram-right-panel.XXXXXX)"
cd desktop
OPENPROGRAM_WEB_PORT=18200 \
OPENPROGRAM_DESKTOP_URL=http://127.0.0.1:18200/chat \
npm run dev -- --user-data-dir="$RIGHT_PANEL_PROFILE_DIR"
```

Expected: Electron receives an argument of the form `--user-data-dir=/tmp/openprogram-right-panel.…`, opens `http://127.0.0.1:18200/chat`, and `http://127.0.0.1:9223/json/version` responds. Do not use an environment variable such as `ELECTRON_EXTRA_LAUNCH_ARGS`; `desktop/main.js` does not read one.

- [ ] **Step 3: Measure the collapsed and expanded geometry through CDP**

In the renderer target, evaluate:

```js
(() => {
  const sidebar = document.querySelector("#rightSidebar");
  const rail = document.querySelector(".right-sidebar-rail");
  const panel = document.querySelector(".right-sidebar-panel");
  const header = document.querySelector(".right-sidebar-panel-header");
  const style = panel ? getComputedStyle(panel) : null;
  return {
    sidebarWidth: sidebar?.getBoundingClientRect().width,
    railWidth: rail?.getBoundingClientRect().width,
    panelWidth: panel?.getBoundingClientRect().width,
    headerHeight: header?.getBoundingClientRect().height,
    panelMargin: style?.margin,
    panelRadius: style?.borderRadius,
    panelPosition: style?.position,
  };
})()
```

Expected when closed: `sidebarWidth === 49` and `railWidth === 49`. Expected after opening Bookmarks at the default width: `railWidth === 49`, `panelWidth === 320`, `headerHeight === 40`, `panelMargin === "8px"`, `panelRadius === "16px"`, and the panel is a normal child of the layout-occupying sidebar shell.

- [ ] **Step 4: Verify rail toggle, Escape, focus restoration, and keyboard resize**

Use pointer and keyboard input, then inspect `document.activeElement` and the separator attributes:

1. Open Bookmarks, click Bookmarks again, and confirm the panel closes while the Bookmarks button remains focused.
2. Reopen Bookmarks, focus the search field, press `Escape`, and confirm the panel closes and focus moves to the Bookmarks rail button.
3. Focus `.right-panel-resize`; press `ArrowLeft` once and confirm `aria-valuenow` changes from `320` to `336`; press `ArrowRight` once and confirm it returns to `320`.
4. Press `Home` and confirm `aria-valuenow === "280"`; press another `ArrowRight` and confirm it remains `280`.
5. Press `End` and confirm `aria-valuenow === "560"`; press another `ArrowLeft` and confirm it remains `560`.

Expected: every control remains keyboard reachable, no focus is lost to `body`, and resize changes do not reload or close any center tab.

- [ ] **Step 5: Verify narrow-window layout and native page bounds**

Open a real web tab, then open the right panel and resize the Electron window below 900 DIP. Through CDP, compare `.center-body`, `.right-sidebar-panel`, and `.right-sidebar-rail` rectangles. Through the existing web-tab bridge/CDP target, inspect the active native view bounds.

Expected: the right shell remains a flex layout participant rather than `position: fixed`; the center rectangle ends before the panel begins; the native `WebContentsView` right edge does not enter the panel rectangle; closing the panel increases the center width by the panel width plus its two 8 DIP gaps without reloading the page.

- [ ] **Step 6: Accept the web-toolbar star add/remove entry independently**

1. Open a real page with a unique URL, for example `https://example.com/?bookmark-toolbar-acceptance=1`.
2. Click the toolbar star and confirm its accessible label changes from `Bookmark` to `Remove bookmark` and its icon becomes filled.
3. Open Bookmarks and a new-tab page in turn; confirm the new item is visible in both surfaces without reload.
4. Return to the same web page, click the filled star, and confirm the accessible label returns to `Bookmark`.
5. Confirm the item disappears from Bookmarks and the new-tab page without reload.

Expected: the toolbar star independently performs both add and remove, and every consumer updates immediately.

- [ ] **Step 7: Accept the new-tab-page bookmark shortcut independently**

1. Open `https://example.org/?bookmark-ntp-acceptance=1` and add it with the toolbar star.
2. Open a new-tab page and confirm its bookmark shortcut shows the page title and carries the exact URL in its title attribute.
3. Click the shortcut title and confirm it opens the bookmarked URL in a normal web tab.
4. Return to a new-tab page, click that shortcut's `Delete bookmark` control, and confirm the shortcut disappears immediately.
5. Return to the web page and confirm the toolbar star is no longer filled.

Expected: the new-tab-page open/delete entry works independently of the manager. This is a separate acceptance case from the toolbar-star add/remove case.

- [ ] **Step 8: Accept manager search, rename, split-open, full-open, delete, and reload persistence**

1. Add two real pages with distinct titles and URLs through the toolbar star, including `https://example.com/?bookmark-manager-acceptance=1`.
2. Open Bookmarks and search for the first item by a unique title substring; confirm only the title match remains.
3. Clear the query, search by a unique URL substring, and confirm only the URL match remains.
4. Rename the first item, press `Enter`, and confirm the renamed title appears immediately in the manager and new-tab page while the URL is unchanged.
5. With a session active and desktop split available, click the renamed title; confirm the page opens beside chat and the panel closes.
6. Reopen Bookmarks and click the explicit `Open in full tab` action for the second item; confirm it activates as a normal full web tab rather than replacing the split target.
7. Reopen Bookmarks, reload the renderer while `bookmarks` is selected, and confirm the right panel restores to Bookmarks rather than Files; both items and the renamed title persist.
8. Delete the first item through the manager, reload again, and confirm it remains absent from the manager, toolbar state, and new-tab page.

Expected: title/URL search, rename, split-open, full-tab-open, manager delete, view restore, and profile-local persistence all work with real renderer state.

- [ ] **Step 9: Verify real cross-window storage updates**

Using the multi-window implementation and the same isolated `--user-data-dir`, open windows A and B. Keep Bookmarks open in B. In A, add a third unique bookmark from the toolbar, rename it in A's manager, then delete it.

Expected: B shows the added item, its renamed title, and its removal after each operation without reload. Confirm B receives the native browser `storage` event for `BOOKMARKS_STORAGE_KEY`; the same-window custom event alone is not sufficient for this acceptance. Do not replace this step with two tabs in one renderer.

- [ ] **Step 10: Run the final regression commands**

Run:

```bash
cd web
npm run check
npx tsc --noEmit
npm run build
cd ../desktop
npm run check:webtabs
cd ..
python -m pytest tests/unit/test_webtab_control.py -q
git diff --check
```

Expected: every command exits `0`; no check relies on the stable 18100 application.

- [ ] **Step 11: Commit only live-discovered fixes**

If live verification exposed no issue, make no commit. If it exposed an issue, first add a failing executable assertion to `web/scripts/check-bookmarks.mjs` or `web/lib/right-panel-behavior.ts`, implement the smallest covering fix, rerun Steps 4–10, then commit only those files:

```bash
git add web/scripts/check-bookmarks.mjs \
  web/lib/right-panel-behavior.ts \
  web/components/right-sidebar/right-sidebar.tsx \
  web/app/styles/right-dock.css \
  web/lib/bookmarks.ts web/lib/session-store/index.ts
git commit -m "fix(desktop): correct compact right panel behavior"
```
