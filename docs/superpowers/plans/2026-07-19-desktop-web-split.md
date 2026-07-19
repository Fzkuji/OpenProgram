# Desktop Web Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep one live Electron web tab visible and model-controllable beside the active chat, with persistent resizing and safe full-width/narrow-window fallbacks.

**Architecture:** Add split selection and ratio fields to the existing center-tabs Zustand store, persisted under a separate `openprogram.webSplit` key. `AppShell` remains the layout owner and mounts the existing `WebTabPane` in either full-width or split position. Renderer-local readiness in `desktop-bridge.ts` connects visible non-zero bounds to `webtab.command(open|active)` without changing the Electron main-process single-view model.

**Tech Stack:** TypeScript, React 18, Zustand 5, Next.js 14, Electron `WebContentsView`, Node assertion checks.

## Global Constraints

- Desktop split shows exactly one native `WebContentsView`; do not create a second `BrowserWindow` or change `desktop/main.js` visibility semantics.
- Default chat ratio is `0.44`; persisted ratios clamp to `0.30..0.70`.
- Minimum chat width is `360` DIP, minimum web width is `480` DIP, divider width is `6` DIP.
- A visible split keeps the session `activeId`; full-width narrow fallback may activate the web tab but must preserve the `/s/...` or `/chat` route.
- Native view readiness means non-zero unobstructed bounds. Radix dialogs, `.branches-merge-modal-backdrop`, and `[data-native-view-occluder="true"]` make it not ready and set zero bounds.
- Reuse current `WebTabPane`, `ResizeObserver`, center-tabs store, right-dock state, and existing browser-control bridge. Add no dependency.

---

### Task 1: Split state and persistence

**Files:**
- Create: `web/scripts/check-web-split.mjs`
- Modify: `web/lib/state/center-tabs-store.ts`
- Modify: `web/package.json`

**Interfaces:**
- Produces: `splitWebTabId: string | null`, `splitRatio: number`, `setSplitWebTab(id)`, `setSplitRatio(ratio)`, `openWebTabInSplit(url): string`.
- Persists: `openprogram.webSplit` as `{ "tabId": string | null, "ratio": number }`.

- [ ] **Step 1: Write the failing state check**

Create `check-web-split.mjs` with the same `window`/`localStorage` stubs used by `check-multi-draft.mjs`, import `useCenterTabs`, and assert:

```js
useCenterTabs.setState({
  tabs: [{ id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" }],
  activeId: "s:chat",
  splitWebTabId: null,
  splitRatio: 0.44,
});
const id = useCenterTabs.getState().openWebTabInSplit("https://example.com/");
assert.equal(id, "w:https://example.com/");
assert.equal(useCenterTabs.getState().activeId, "s:chat");
assert.equal(useCenterTabs.getState().splitWebTabId, id);
useCenterTabs.getState().setSplitRatio(5);
assert.equal(useCenterTabs.getState().splitRatio, 0.70);
useCenterTabs.getState().closeTab(id);
assert.equal(useCenterTabs.getState().splitWebTabId, null);
```

Register the exact script and insert it into `npm run check` before `check:multi-draft`:

```json
"check:web-split": "node --no-warnings --experimental-strip-types scripts/check-web-split.mjs"
```

- [ ] **Step 2: Run the check and verify RED**

Run: `cd web && npm run check:web-split`

Expected: failure because `openWebTabInSplit` and split fields do not exist.

- [ ] **Step 3: Implement minimal split state**

Add constants and helpers in `center-tabs-store.ts`:

```ts
const SPLIT_STORAGE_KEY = "openprogram.webSplit";
const DEFAULT_SPLIT_RATIO = 0.44;
const MIN_SPLIT_RATIO = 0.30;
const MAX_SPLIT_RATIO = 0.70;
const clampSplitRatio = (ratio: number) =>
  Math.min(MAX_SPLIT_RATIO, Math.max(MIN_SPLIT_RATIO, ratio));
```

Read the separate payload after tabs restore, accept only an existing `kind === "web"` id, and persist changes from the three split actions. `openWebTabInSplit` appends or reuses the deterministic web tab without consuming the active NTP and returns its id. Update `closeTab` to clear/persist a matching split id.

- [ ] **Step 4: Run focused and neighboring checks**

Run:

```bash
cd web
npm run check:web-split
npm run check:multi-draft
```

Expected: both commands print their pass message and exit `0`.

- [ ] **Step 5: Commit**

```bash
git add web/lib/state/center-tabs-store.ts web/scripts/check-web-split.mjs web/package.json
git commit -m "feat(tabs): add persistent web split state"
```

### Task 2: Native pane lifecycle and readiness

**Files:**
- Modify: `web/lib/desktop-bridge.ts`
- Modify: `web/components/center-tabs/web-tab-pane.tsx`
- Modify: `web/components/chat/composer/attach/file-tiles.tsx`
- Modify: `web/scripts/check-web-split.mjs`

**Interfaces:**
- Produces: `setWebTabReady(id: string, ready: boolean): void`, `isWebTabReady(id: string): boolean`, `waitForWebTabReady(id: string, timeoutMs: number): Promise<boolean>`.
- Produces: `setDesktopSplitLayoutAvailable(available: boolean): void`, `isDesktopSplitLayoutAvailable(): boolean`.

- [ ] **Step 1: Extend the check for readiness and lifecycle source**

Add assertions that readiness immediately resolves for an already-ready id, times out to `false`, and clears on `setWebTabReady(id, false)`. Add source checks requiring `web-tab-pane.tsx` to call `setWebTabReady`, forbidding the unconditional mount-time line `bridge.webTab.navigate(tabId, viewUrlRef.current)`, and requiring `data-native-view-occluder="true"` on the attachment preview backdrop.

- [ ] **Step 2: Run the check and verify RED**

Run: `cd web && npm run check:web-split`

Expected: failure on missing readiness exports or forbidden mount navigation.

- [ ] **Step 3: Add the renderer-local registry**

In `desktop-bridge.ts`, use a `Set<string>` and waiter map. `waitForWebTabReady` must return immediately when ready, otherwise resolve `true` on the next ready transition or `false` after the exact timeout. Clearing readiness must not resolve waiters as success.

In `DesktopWebTabPane`:

```ts
const occluded = !!document.querySelector(
  '[role="dialog"], .branches-merge-modal-backdrop, [data-native-view-occluder="true"]',
);
if (occluded || r.width <= 0 || r.height <= 0) {
  bridge.webTab.setBounds(tabId, { x: 0, y: 0, width: 0, height: 0 });
  setWebTabReady(tabId, false);
  return;
}
bridge.webTab.setBounds(tabId, roundedBounds);
setWebTabReady(tabId, true);
```

Observe body child/attribute changes while mounted, clear readiness on cleanup, remove the unconditional mount `navigate`, and mark the file preview portal as an occluder.

- [ ] **Step 4: Run the focused check**

Run: `cd web && npm run check:web-split`

Expected: pass with no unhandled timeout.

- [ ] **Step 5: Commit**

```bash
git add web/lib/desktop-bridge.ts web/components/center-tabs/web-tab-pane.tsx web/components/chat/composer/attach/file-tiles.tsx web/scripts/check-web-split.mjs
git commit -m "fix(desktop): preserve and guard native web views"
```

### Task 3: Split layout and controls

**Files:**
- Modify: `web/components/app-shell.tsx`
- Modify: `web/components/center-tabs/web-tab-pane.tsx`
- Modify: `web/components/center-tabs/center-tab-strip.tsx`
- Modify: `web/components/center-tabs/center-tabs.module.css`
- Modify: `web/app/styles/base.css`
- Modify: `web/scripts/check-web-split.mjs`

**Interfaces:**
- Consumes: split store fields/actions and `setDesktopSplitLayoutAvailable`.
- Produces: toolbar enter/exit controls, `.center-split-chat`, `.center-split-divider`, `.center-split-web`, and `data-split-pinned` on the pinned tab.

- [ ] **Step 1: Add failing structural checks**

Require the app shell source to reference `splitWebTabId`, `splitRatio`, a `ResizeObserver`, the exact minimum-width sum `846`, and one conditional split `WebTabPane`. Require the toolbar to expose Chinese/English accessible labels for `Open split view`/`Exit split view`, and the tab strip to set `data-split-pinned`.

- [ ] **Step 2: Run the check and verify RED**

Run: `cd web && npm run check:web-split`

Expected: failure on the missing split layout and controls.

- [ ] **Step 3: Implement the split layout**

In `AppShell`, observe `.center-body`, derive `splitAvailable = width >= 846`, and only render the secondary pane when desktop, active kind is session, the split id resolves to a web tab, and space is available. Wrap the singleton chat in a flex item whose width is `${splitRatio * 100}%`; keep the web side `flex: 1; min-width: 0`.

```tsx
const centerBodyRef = useRef<HTMLDivElement>(null);
const [centerBodyWidth, setCenterBodyWidth] = useState(0);
useEffect(() => {
  const node = centerBodyRef.current;
  if (!node) return;
  const update = () => setCenterBodyWidth(node.getBoundingClientRect().width);
  update();
  const observer = new ResizeObserver(update);
  observer.observe(node);
  return () => observer.disconnect();
}, []);
const splitAvailable = centerBodyWidth >= 846;
const showSplit =
  isDesktop && activeKind === "session" && !!splitTab && splitAvailable;
useEffect(() => {
  setDesktopSplitLayoutAvailable(
    isDesktop && activeKind === "session" && splitAvailable,
  );
}, [isDesktop, activeKind, splitAvailable]);
```

```tsx
<div ref={centerBodyRef} className="center-body">
  <div
    className={showSplit ? "center-split-chat" : "center-single-pane"}
    style={showSplit ? { width: `${splitRatio * 100}%` } : undefined}
  >
    <PageShell page="chat" />
  </div>
  {showSplit ? (
    <>
      <div className="center-split-divider" role="separator" aria-orientation="vertical" />
      <div className="center-split-web">
        <WebTabPane tabId={splitTab.id} url={splitTab.url ?? ""} />
      </div>
    </>
  ) : null}
</div>
```

The divider mousemove computes `(clientX - body.left) / body.width`, clamps dynamically so both minimum widths remain satisfied, then calls `setSplitRatio`. It has `role="separator"`, `aria-orientation="vertical"`, and a keyboard handler changing the ratio by `0.02` on left/right arrows.

The web toolbar split button pins the current tab, selects the session represented by the current route/session state (or creates a draft through the existing path), and collapses the right dock. Exit clears only `splitWebTabId`. The tab strip marker must not replace the normal active styling.

```ts
function enterSplit() {
  const state = useCenterTabs.getState();
  const sid = window.location.pathname.startsWith("/s/")
    ? decodeURIComponent(window.location.pathname.slice(3))
    : useSessionStore.getState().activeChatKey;
  const session = state.tabs.find(
    (tab) => tab.kind === "session" && tab.sessionId === sid,
  );
  state.setSplitWebTab(tabId);
  useSessionStore.getState().setRightDockOpen(false);
  if (session) state.setActive(session.id);
  else {
    const draftId = state.openDraftSessionTab();
    (window as unknown as { newSession?: (id?: string) => void })
      .newSession?.(draftId);
  }
}
```

- [ ] **Step 4: Run checks and TypeScript build**

Run:

```bash
cd web
npm run check:web-split
npm run check:center-tabs
npm run build
```

Expected: all exit `0`; build reports successful compilation.

- [ ] **Step 5: Commit**

```bash
git add web/components/app-shell.tsx web/components/center-tabs/web-tab-pane.tsx web/components/center-tabs/center-tab-strip.tsx web/components/center-tabs/center-tabs.module.css web/app/styles/base.css web/scripts/check-web-split.mjs
git commit -m "feat(desktop): add resizable chat web split"
```

### Task 4: Agent control targets the visible split

**Files:**
- Modify: `web/lib/desktop-bridge.ts`
- Modify: `web/scripts/check-web-split.mjs`
- Test: `tests/unit/test_webtab_control.py`

**Interfaces:**
- Consumes: `openWebTabInSplit`, readiness registry, and split-layout availability.
- Preserves: `webtab_result` schema `{ ok, url, tab_id, target_id, error? }`.

- [ ] **Step 1: Write failing selection checks**

Add source-level assertions in `check-web-split.mjs` that the `webtab.command` handler considers both active full-width web and ready split ids, waits with `2000`, and never calls `showCenterSurface()` when already on `/s/...`. Add a Python source check that the renderer contract contains the split target path.

- [ ] **Step 2: Run checks and verify RED**

Run:

```bash
cd web && npm run check:web-split
cd .. && python -m pytest tests/unit/test_webtab_control.py -q
```

Expected: at least the new split-target assertion fails.

- [ ] **Step 3: Implement target resolution**

For `op=active`, select an active web tab only when ready; otherwise select `splitWebTabId` only when the active tab is a session and that split id is ready. For `op=open`, use `openWebTabInSplit` when a session is active and the layout is available, await readiness for `2000`, then activate. When unavailable, use normal `openWebTab` without navigating an existing `/s/...` route to `/chat`. If fallback activation fails, restore the prior session id.

```ts
function visibleWebTab() {
  const state = useCenterTabs.getState();
  const active = state.tabs.find((tab) => tab.id === state.activeId);
  if (active?.kind === "web" && isWebTabReady(active.id)) return active;
  const split = state.tabs.find((tab) => tab.id === state.splitWebTabId);
  return active?.kind === "session" && split?.kind === "web"
    && isWebTabReady(split.id) ? split : null;
}
```

```ts
const priorActiveId = state.activeId;
const split = active?.kind === "session" && isDesktopSplitLayoutAvailable();
const id = split
  ? state.openWebTabInSplit(d.url)
  : (state.openWebTab(d.url), useCenterTabs.getState().activeId);
const ready = !!id && await waitForWebTabReady(id, 2000);
const tab = id ? useCenterTabs.getState().tabs.find((item) => item.id === id) : null;
const targetId = ready && tab?.kind === "web"
  ? await bridge.webTab.activate(tab.id, tab.url)
  : null;
if (!targetId && !split && priorActiveId) {
  useCenterTabs.getState().setActive(priorActiveId);
}
```

- [ ] **Step 4: Run control-plane checks**

Run:

```bash
cd web && npm run check:web-split
cd .. && node desktop/scripts/check-webtab-navigation.js
python -m pytest tests/unit/test_webtab_control.py -q
```

Expected: web split check passes, desktop script prints `webtab navigation checks passed`, and pytest reports all tests passed.

- [ ] **Step 5: Commit**

```bash
git add web/lib/desktop-bridge.ts web/scripts/check-web-split.mjs tests/unit/test_webtab_control.py
git commit -m "feat(browser): control the visible split web tab"
```
