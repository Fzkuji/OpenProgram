// Ownership rules for the web split: split controls and session navigation
// belong to the tab and pane layer, native web views are placed only through
// the bounds registry, and full-window overlays occlude native views.
// Behaviour lives in tests/tabs/web-split/*.test.mjs.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

const web = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, web), "utf8").replaceAll("\r\n", "\n");
const readDir = (path) => readdirSync(new URL(path, web)).filter((name) => /\.tsx?$/.test(name)).sort().map((name) => read(path + name));

// The browser pane: its entry file and the pieces under pane/.
const webTabPane = [read("components/center-tabs/browser/web-tab-pane.tsx"), ...readDir("components/center-tabs/browser/pane/")].join("\n");
const lifecycle = read("components/center-tabs/tab-actions/use-tab-lifecycle.ts");
const appShell = read("components/app-shell.tsx");
const pip = read("components/center-tabs/pip/web-tab-pip.tsx");

function functionBody(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found`);
  const next = source.indexOf("\n  function ", start + 1);
  return source.slice(start, next < 0 ? undefined : next);
}

// 1. Split controls belong to the tab and pane layer, not the browser toolbar.
assert.doesNotMatch(webTabPane, /Open split view|Exit split view|SplitViewPicker|Columns2/,
  "split controls belong to the tab and pane layer, not the browser toolbar");
assert.doesNotMatch(webTabPane, /setSplitWebTab\(/, "the browser pane never changes the split");
// 2. The browser pane never creates or switches chat sessions.
assert.doesNotMatch(webTabPane, /openDraftSessionTab\(|newSession\(/, "the browser pane never opens a session");
// 3. Native web views are placed only through the visible-bounds registry.
assert.doesNotMatch(webTabPane, /bridge\.webTab\.(?:show|hide|setBounds)\(/,
  "the pane registers bounds; the bridge alone shows, hides and moves native views");
// 4. The iframe pane (plain browser) never touches native views.
assert.doesNotMatch(webTabPane.slice(webTabPane.indexOf("function IframeWebTabPane")), /ensureWebView|registerVisibleWebTabBounds/,
  "the iframe pane must not create or place native views");
// 5. The floating preview shows pictures only: it never hosts a live page.
assert.doesNotMatch(pip, /<iframe|ensureWebView|registerVisibleWebTabBounds|setPipZoom/,
  "the PiP renders snapshots, never a live web view");
// 6. Session navigation has one trigger, the active-tab effect: a tab click
//    and the close path only change the store.
assert.doesNotMatch(functionBody(lifecycle, "onTabClick"), /activateSession/, "onTabClick must not navigate sessions itself");
assert.doesNotMatch(functionBody(lifecycle, "finishClose"), /activateSession/, "finishClose must not navigate sessions itself");
// 7. The chat shell is a mounted singleton, and leaving the chat route keeps the PiP binding.
assert.equal(appShell.match(/<PageShell page="chat" \/>/g)?.length, 1, "the chat shell must remain a mounted singleton");
assert.doesNotMatch(appShell, /useWebTabPip\.getState\(\)\.hide\(\)/, "leaving the chat route must keep the PiP binding");
// 8. Full-window backdrops occlude native views (they paint over the DOM otherwise).
for (const [path, overlay] of [["components/ui/dialog.tsx", "DialogPrimitive.Overlay"], ["components/ui/alert-dialog.tsx", "AlertDialogPrimitive.Overlay"]]) {
  assert.match(read(path), new RegExp(`<${overlay.replace(".", "\\.")}[\\s\\S]*?data-native-view-occluder="true"`),
    `${path}: the full-window backdrop must occlude native web views`);
}
