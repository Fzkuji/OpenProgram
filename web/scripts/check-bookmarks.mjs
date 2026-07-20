import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import ts from "typescript";

const sourcePath = new URL("../lib/bookmarks.ts", import.meta.url);
const sessionStorePath = new URL("../lib/session-store/index.ts", import.meta.url);
const navigationPath = new URL("../lib/bookmark-navigation.ts", import.meta.url);
const webTabPath = new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url);
const newTabPath = new URL("../components/center-tabs/new-tab-page.tsx", import.meta.url);
// Bookmarks + web history are CENTER TABS now, opened from the main
// menu — not right-sidebar views. These paths are the new landing spot;
// the assertions below are the same guard ("the feature exists and is
// reachable") pointed at it.
const managerPath = new URL("../components/center-tabs/builtin-tab-pane.tsx", import.meta.url);
const mainMenuPath = new URL("../components/center-tabs/main-menu.tsx", import.meta.url);
const stripPath = new URL("../components/center-tabs/center-tab-strip.tsx", import.meta.url);
const appShellPath = new URL("../components/app-shell.tsx", import.meta.url);
const tabsStorePath = new URL("../lib/state/center-tabs-store.ts", import.meta.url);
const rightSidebarPath = new URL("../components/right-sidebar/right-sidebar.tsx", import.meta.url);
const rightDockCssPath = new URL("../app/styles/right-dock.css", import.meta.url);
const packagePath = new URL("../package.json", import.meta.url);
assert.ok(existsSync(sourcePath), "bookmarks storage module missing");
assert.ok(existsSync(managerPath), "bookmarks/history builtin tab pane missing");
assert.ok(existsSync(mainMenuPath), "main menu missing");
// The old right-sidebar panels were absorbed by the builtin tab pane.
assert.equal(
  existsSync(new URL("../components/right-sidebar/bookmarks-panel.tsx", import.meta.url)),
  false,
  "the right-sidebar bookmarks panel must stay deleted",
);
assert.equal(
  existsSync(new URL("../components/right-sidebar/browsing-history-panel.tsx", import.meta.url)),
  false,
  "the right-sidebar browsing-history panel must stay deleted",
);

const packageJson = JSON.parse(readFileSync(packagePath, "utf8"));
assert.equal(
  packageJson.scripts?.["check:bookmarks"],
  "node --no-warnings scripts/check-bookmarks.mjs",
);
assert.match(packageJson.scripts?.check || "", /check:bookmarks/);

const source = readFileSync(sourcePath, "utf8");
const sessionStore = readFileSync(sessionStorePath, "utf8");
const webTab = readFileSync(webTabPath, "utf8");
const newTab = readFileSync(newTabPath, "utf8");
const manager = readFileSync(managerPath, "utf8");
const mainMenu = readFileSync(mainMenuPath, "utf8");
const strip = readFileSync(stripPath, "utf8");
const appShell = readFileSync(appShellPath, "utf8");
const tabsStore = readFileSync(tabsStorePath, "utf8");
const rightSidebar = readFileSync(rightSidebarPath, "utf8");
const rightDockCss = readFileSync(rightDockCssPath, "utf8");
assert.match(webTab, /function BookmarkButton/);
assert.match(webTab, /toggleBookmark\(\{ url, title \}\)/);
assert.match(webTab, /<BookmarkButton url=\{effectiveUrl\} title=\{title \|\| effectiveUrl\} \/>/);
assert.match(newTab, /readBookmarks/);
assert.match(newTab, /removeBookmark/);
// The right dock no longer has a bookmarks view; a stale persisted
// "bookmarks" value must fall back rather than restore a dead view.
assert.doesNotMatch(
  sessionStore,
  /const VALID_VIEWS = new Set\(\[[^\]]*"bookmarks"[^\]]*\]\);/s,
  "the right-dock bookmarks view must stay removed",
);
for (const [name, text] of [
  ["web-tab-pane.tsx", webTab],
  ["new-tab-page.tsx", newTab],
  ["builtin-tab-pane.tsx", manager],
]) {
  assert.match(text, /subscribeBookmarks\(refresh\)/, `${name} must use shared bookmark subscription`);
}
assert.match(manager, /bookmark\.title\.toLowerCase\(\)\.includes\(needle\)/);
assert.match(manager, /bookmark\.url\.toLowerCase\(\)\.includes\(needle\)/);
assert.match(manager, /renameBookmark\(url,\s*draftTitle\)/);
assert.match(manager, /removeBookmark\(bookmark\.url\)/);
assert.match(manager, /openWebTab\(bookmark\.url\)/);
assert.match(manager, /text\("No bookmarks yet",\s*"还没有书签"\)/);
assert.match(manager, /text\("No matching bookmarks",\s*"没有匹配的书签"\)/);

// ---- Reachability: main menu → builtin center tab -------------------
// The two library pages must stay openable. The menu is the entry
// point, the store action is the singleton, the shell renders it.
assert.match(mainMenu, /openBuiltinTab\("bookmarks"\)/, "main menu must open bookmarks");
assert.match(mainMenu, /openBuiltinTab\("history"\)/, "main menu must open web history");
assert.match(mainMenu, /router\.push\("\/settings"\)/, "main menu must reach settings");
assert.match(mainMenu, /openNewTabPage\(\)/, "main menu must open a new tab");
// Flat menu — no submenus, by design.
assert.doesNotMatch(mainMenu, /DropdownMenuSub/, "the main menu must stay one flat level");
// Look comes from menu-styles, never a bespoke panel.
assert.match(mainMenu, /MENU_PANEL/);
assert.match(mainMenu, /itemCls\(false\)/);
assert.match(mainMenu, /MENU_SEPARATOR/);
// The menu button sits AFTER the + in the strip (Chrome's ⋮ position).
const plusIndex = strip.indexOf("styles.plusBtn");
const menuIndex = strip.indexOf("<MainMenu />");
assert.ok(plusIndex >= 0, "new-tab + button missing from the strip");
assert.ok(menuIndex >= 0, "main menu button missing from the strip");
assert.ok(plusIndex < menuIndex, "the main menu must come after the + button");
// The + is a natural flex item again — the reserved right column now
// belongs to the menu button, so the old pinning machinery is gone.
assert.doesNotMatch(strip, /data-plus-rail-aligned/, "the + must no longer be pinned to the rail");
// Singleton builtin tabs: deterministic id ⇒ focus-or-create.
assert.match(tabsStore, /export function builtinTabId\(page: BuiltinPage\): string \{\s*return `b:\$\{page\}`;/);
assert.match(
  tabsStore,
  /openBuiltinTab: \(page\) =>\s*set\(\(s\) =>\s*focusOrCreate\(\s*s,\s*builtinTabId\(page\)/,
  "openBuiltinTab must go through focusOrCreate so each page has one tab",
);
// Builtin tabs render in the center, so they work on chat routes.
assert.match(appShell, /tab\.kind === "builtin" && tab\.page/);
assert.match(appShell, /<BuiltinTabPane page=\{tab\.page\} \/>/);
// Right sidebar keeps no bookmarks view or nav entry.
assert.doesNotMatch(rightSidebar, /VIEW_BOOKMARKS|BookmarksPanel|BrowsingHistoryPanel/,
  "the right sidebar must not host bookmarks/history any more");
assert.doesNotMatch(
  rightDockCss,
  /\.right-sidebar\[data-view="bookmarks"\]/,
  "the right-dock bookmarks view rule must stay removed",
);
// Detail / Context are reachable without the legacy global.
assert.match(rightSidebar, /function SessionViewSwitch/);
// Gate on nodeSelected, NOT detailNode: the legacy DAG showDetail paints
// #detailBody itself and never sets detailNode, so gating on detailNode
// stranded users in Detail with no way back to History.
assert.match(
  rightSidebar,
  /const selected = useSessionStore\(\(s\) => s\.nodeSelected\);/,
  "the view switch must gate on nodeSelected so both selection paths show it",
);
const uiBridge = readFileSync(
  new URL("../lib/runtime-bridge/ui.ts", import.meta.url),
  "utf8",
);
assert.match(
  uiBridge,
  /setNodeSelected\(true\)/,
  "the legacy DAG showDetail must flag the selection for the React switch",
);
assert.match(
  uiBridge,
  /setNodeSelected\(false\)/,
  "closing the detail panel must clear the selection flag",
);
// It must NOT set detailNode — that would double-render the panel.
assert.doesNotMatch(
  uiBridge,
  /showDetail: \(node\)|setState\(\{ detailNode/,
  "the legacy bridge must not populate detailNode (React would render a second copy)",
);
assert.match(rightSidebar, /<SessionViewSwitch current=\{VIEW_DETAIL\} \/>/);
assert.match(rightSidebar, /<SessionViewSwitch current=\{VIEW_CONTEXT\} \/>/);
assert.match(rightSidebar, /<SessionViewSwitch current=\{VIEW_HISTORY\} \/>/);
assert.match(
  rightDockCss,
  /\.bookmarks-search input:focus-visible\s*\{[^}]*outline:\s*(?!0)[^;}]+;/s,
);
assert.match(
  rightDockCss,
  /\.bookmark-title-input:focus-visible\s*\{[^}]*outline:\s*(?!0)[^;}]+;/s,
);
// Flat color-block panel: the compact "floating card" wrapper
// (.right-sidebar-panel with margin/radius/shadow, reverted in
// 6464cdaa) must not come back — the panel stays full-height,
// edge-to-edge, no rounded card chrome.
assert.doesNotMatch(rightDockCss, /right-sidebar-panel/, "floating card wrapper resurfaced");
assert.doesNotMatch(rightSidebar, /right-sidebar-panel|rounded-(?:lg|xl|2xl|3xl)/, "right sidebar shell must stay flat");

// The bookmarks nav row is gone from the icon rail (it is a main-menu
// entry now, asserted above). The rail keeps only the top-level
// destinations that describe the CURRENT session/context.
assert.match(rightSidebar, /data-view=\{VIEW_HISTORY\}/, "history rail entry missing");
assert.match(rightSidebar, /data-view=\{VIEW_FILES\}/, "files rail entry missing");

function parseTsx(text, name) {
  return ts.createSourceFile(name, text, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
}

function attr(opening, name) {
  return opening.attributes.properties.find(
    (property) => ts.isJsxAttribute(property) && property.name.text === name,
  );
}

function assertNoNestedButtons(text, name) {
  const file = parseTsx(text, name);
  function visit(node, insideButton = false) {
    if (ts.isJsxElement(node)) {
      const isButton = node.openingElement.tagName.getText(file) === "button";
      assert.ok(!(insideButton && isButton), `${name} contains nested buttons`);
      for (const child of node.children) visit(child, insideButton || isButton);
      return;
    }
    ts.forEachChild(node, (child) => visit(child, insideButton));
  }
  visit(file);
}

assertNoNestedButtons(manager, "builtin-tab-pane.tsx");
assertNoNestedButtons(rightSidebar, "right-sidebar.tsx");

const managerFile = parseTsx(manager, "builtin-tab-pane.tsx");
// One file hosts both pages; scope the walk to BookmarksPage so the
// history page's own .bookmark-actions group isn't what we measure.
let bookmarksPage;
function findBookmarksPage(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "BookmarksPage") {
    bookmarksPage = node;
    return;
  }
  ts.forEachChild(node, findBookmarksPage);
}
findBookmarksPage(managerFile);
assert.ok(bookmarksPage, "BookmarksPage component missing");
let actions;
function findActions(node) {
  if (
    ts.isJsxElement(node) &&
    node.openingElement.tagName.getText(managerFile) === "div" &&
    attr(node.openingElement, "className")?.initializer?.text === "bookmark-actions"
  ) {
    actions = node;
    return;
  }
  ts.forEachChild(node, findActions);
}
findActions(bookmarksPage);
assert.ok(actions, "bookmark action group missing");
const iconButtons = [];
function collectButtons(node) {
  if (
    ts.isJsxElement(node) &&
    node.openingElement.tagName.getText(managerFile) === "button"
  ) {
    iconButtons.push(node.openingElement);
  }
  ts.forEachChild(node, collectButtons);
}
collectButtons(actions);
// Wide page: the row title itself opens the bookmark, so there is no
// separate "open in full tab" control — edit/save/cancel/delete only.
assert.equal(iconButtons.length, 4, "expected edit/save/cancel/delete controls");
for (const button of iconButtons) {
  assert.ok(attr(button, "title"), "icon-only bookmark control missing title");
  assert.ok(attr(button, "aria-label"), "icon-only bookmark control missing aria-label");
  assert.equal(attr(button, "type")?.initializer?.text, "button");
}
for (const [label, en, zh] of [
  ["saveLabel", "Save bookmark title", "保存书签标题"],
  ["cancelLabel", "Cancel editing", "取消编辑"],
  ["editLabel", "Edit bookmark title", "编辑书签标题"],
  ["deleteLabel", "Delete bookmark", "删除书签"],
]) {
  assert.match(manager, new RegExp(`const ${label} = text\\("${en}",\\s*"${zh}"\\)`));
}
assert.doesNotMatch(manager, /setBookmarks\((?:renameBookmark|removeBookmark)\(/);

assert.ok(existsSync(navigationPath), "bookmark navigation module missing");
const navigationSource = readFileSync(navigationPath, "utf8");
const navigationCompiled = ts.transpileModule(navigationSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const navigation = await import(
  `data:text/javascript;base64,${Buffer.from(navigationCompiled).toString("base64")}`,
);
for (const desktop of [false, true]) {
  for (const sessionActive of [false, true]) {
    for (const splitAvailable of [false, true]) {
      const calls = [];
      navigation.openBookmark("https://example.com/", {
        desktop,
        activeKind: sessionActive ? "session" : "web",
        splitAvailable,
        openSplit: (url) => calls.push(["split", url]),
        openTab: (url) => calls.push(["tab", url]),
        collapseDock: () => calls.push(["collapse"]),
      });
      assert.deepEqual(
        calls,
        desktop && sessionActive && splitAvailable
          ? [["split", "https://example.com/"], ["collapse"]]
          : [["tab", "https://example.com/"]],
      );
    }
  }
}
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const bookmarks = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`,
);

const storage = new Map();
const windowEvents = new EventTarget();
let failWrites = false;
globalThis.window = windowEvents;
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => {
    if (failWrites) throw new Error("storage disabled");
    storage.set(key, value);
  },
};

let changes = 0;
windowEvents.addEventListener(bookmarks.BOOKMARKS_CHANGE_EVENT, () => changes++);

const first = { title: "Example", url: "https://example.com/" };
const second = { title: "OpenProgram", url: "https://openprogram.ai/" };

assert.deepEqual(bookmarks.readBookmarks(), []);
storage.set(bookmarks.BOOKMARKS_STORAGE_KEY, "not-json");
assert.deepEqual(bookmarks.readBookmarks(), []);
assert.deepEqual(bookmarks.toggleBookmark(first), [first]);
assert.equal(storage.get(bookmarks.BOOKMARKS_STORAGE_KEY), JSON.stringify([first]));
assert.equal(changes, 1);
assert.deepEqual(bookmarks.toggleBookmark(first), []);
assert.equal(storage.get(bookmarks.BOOKMARKS_STORAGE_KEY), "[]");

storage.set(bookmarks.BOOKMARKS_STORAGE_KEY, "[");
assert.deepEqual(bookmarks.toggleBookmark(first), [first]);
assert.deepEqual(bookmarks.toggleBookmark(second), [first, second]);
assert.equal(
  storage.get(bookmarks.BOOKMARKS_STORAGE_KEY),
  JSON.stringify([first, second]),
);
assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
assert.equal(changes, 4);

assert.deepEqual(bookmarks.removeBookmark(first.url), [second]);
assert.equal(storage.get(bookmarks.BOOKMARKS_STORAGE_KEY), JSON.stringify([second]));
assert.equal(changes, 5);

assert.deepEqual(
  bookmarks.renameBookmark(second.url, "  Renamed  "),
  [{ title: "Renamed", url: second.url }],
);
assert.equal(changes, 6);
assert.deepEqual(
  bookmarks.renameBookmark(second.url, "   "),
  [{ title: second.url, url: second.url }],
);
assert.equal(changes, 7);
assert.deepEqual(
  bookmarks.renameBookmark("https://missing.example/", "Missing"),
  [{ title: second.url, url: second.url }],
);
assert.equal(changes, 7);

failWrites = true;
let failedRename;
assert.doesNotThrow(() => {
  failedRename = bookmarks.renameBookmark(second.url, "Failed");
});
assert.deepEqual(failedRename, [{ title: second.url, url: second.url }]);
assert.equal(changes, 7);
assert.equal(
  storage.get(bookmarks.BOOKMARKS_STORAGE_KEY),
  JSON.stringify([{ title: second.url, url: second.url }]),
);
assert.doesNotThrow(() => bookmarks.toggleBookmark(first));
assert.deepEqual(bookmarks.toggleBookmark(first), [{ title: second.url, url: second.url }]);
assert.equal(
  storage.get(bookmarks.BOOKMARKS_STORAGE_KEY),
  JSON.stringify([{ title: second.url, url: second.url }]),
);
assert.equal(changes, 7);

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

console.log("bookmark storage checks passed");
