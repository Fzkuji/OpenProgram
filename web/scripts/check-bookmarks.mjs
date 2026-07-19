import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import ts from "typescript";
import {
  RIGHT_PANEL_DEFAULT,
  RIGHT_PANEL_GAP,
  RIGHT_PANEL_MAX,
  RIGHT_PANEL_MIN,
  RIGHT_RAIL_WIDTH,
  clampPanelWidth,
  handlePanelResizeKey,
  panelWidthAfterKey,
  resetPanelResize,
  resolveRightPanelAction,
} from "../lib/right-panel-behavior.ts";

const sourcePath = new URL("../lib/bookmarks.ts", import.meta.url);
const sessionStorePath = new URL("../lib/session-store/index.ts", import.meta.url);
const navigationPath = new URL("../lib/bookmark-navigation.ts", import.meta.url);
const webTabPath = new URL("../components/center-tabs/web-tab-pane.tsx", import.meta.url);
const newTabPath = new URL("../components/center-tabs/new-tab-page.tsx", import.meta.url);
const managerPath = new URL("../components/right-sidebar/bookmarks-panel.tsx", import.meta.url);
const rightSidebarPath = new URL("../components/right-sidebar/right-sidebar.tsx", import.meta.url);
const rightDockCssPath = new URL("../app/styles/right-dock.css", import.meta.url);
const packagePath = new URL("../package.json", import.meta.url);
assert.ok(existsSync(sourcePath), "bookmarks storage module missing");
assert.ok(existsSync(managerPath), "bookmarks manager missing");

const packageJson = JSON.parse(readFileSync(packagePath, "utf8"));
assert.equal(
  packageJson.scripts?.["check:bookmarks"],
  "node --no-warnings --experimental-strip-types scripts/check-bookmarks.mjs",
);
assert.match(packageJson.scripts?.check || "", /check:bookmarks/);

const source = readFileSync(sourcePath, "utf8");
const sessionStore = readFileSync(sessionStorePath, "utf8");
const webTab = readFileSync(webTabPath, "utf8");
const newTab = readFileSync(newTabPath, "utf8");
const manager = readFileSync(managerPath, "utf8");
const rightSidebar = readFileSync(rightSidebarPath, "utf8");
const rightDockCss = readFileSync(rightDockCssPath, "utf8");

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

const resizeDrag = { current: { startX: 100, startW: RIGHT_PANEL_DEFAULT } };
let resizeActive = true;
const commitResizeActive = (active) => {
  resizeActive = active;
};
resetPanelResize(resizeDrag, commitResizeActive);
assert.equal(resizeDrag.current, null);
assert.equal(resizeActive, false);
assert.doesNotThrow(() => resetPanelResize(resizeDrag, commitResizeActive));
assert.equal(resizeDrag.current, null);
assert.equal(resizeActive, false);

assert.match(webTab, /function BookmarkButton/);
assert.match(webTab, /toggleBookmark\(\{ url, title \}\)/);
assert.match(webTab, /<BookmarkButton url=\{effectiveUrl\} title=\{title \|\| effectiveUrl\} \/>/);
assert.match(newTab, /readBookmarks/);
assert.match(newTab, /removeBookmark/);
assert.match(
  sessionStore,
  /const VALID_VIEWS = new Set\(\[[^\]]*"bookmarks"[^\]]*\]\);/s,
  "Bookmarks must survive right-dock restore",
);
for (const [name, text] of [
  ["web-tab-pane.tsx", webTab],
  ["new-tab-page.tsx", newTab],
  ["bookmarks-panel.tsx", manager],
]) {
  assert.match(text, /subscribeBookmarks\(refresh\)/, `${name} must use shared bookmark subscription`);
}
assert.match(manager, /bookmark\.title\.toLowerCase\(\)\.includes\(needle\)/);
assert.match(manager, /bookmark\.url\.toLowerCase\(\)\.includes\(needle\)/);
assert.match(manager, /renameBookmark\(url,\s*draftTitle\)/);
assert.match(manager, /removeBookmark\(bookmark\.url\)/);
assert.match(manager, /openWebTab\(bookmark\.url\)/);
assert.match(manager, /text\("Open in full tab",\s*"在完整标签页中打开"\)/);
assert.match(manager, /text\("No bookmarks yet",\s*"还没有书签"\)/);
assert.match(manager, /text\("No matching bookmarks",\s*"没有匹配的书签"\)/);
assert.match(rightSidebar, /const VIEW_BOOKMARKS = "bookmarks";/);
assert.match(rightSidebar, /data-view=\{VIEW_BOOKMARKS\}/);
assert.match(rightSidebar, /<BookmarksPanel \/>/);
assert.match(
  rightDockCss,
  /\.right-sidebar\[data-view="bookmarks"\]\s+\.right-view\[data-view="bookmarks"\]\s*\{\s*display:\s*flex;/,
);
assert.match(
  rightDockCss,
  /\.bookmarks-search input:focus-visible\s*\{[^}]*outline:\s*(?!0)[^;}]+;/s,
);
assert.match(
  rightDockCss,
  /\.bookmark-title-input:focus-visible\s*\{[^}]*outline:\s*(?!0)[^;}]+;/s,
);

for (const [selector, declaration] of [
  ["right-sidebar-rail", /width:\s*49px/],
  ["right-sidebar-panel", /margin:\s*8px/],
  ["right-sidebar-panel", /border:\s*1px solid var\(--border-popover\)/],
  ["right-sidebar-panel", /border-radius:\s*16px/],
  ["right-sidebar-panel", /box-shadow:\s*var\(--shadow-popover\)/],
  ["right-sidebar-panel-header", /height:\s*40px/],
]) {
  const rule = new RegExp(`\\.${selector}\\s*\\{[^}]*${declaration.source}`, "s");
  assert.match(rightDockCss, rule, `${selector} geometry missing`);
}

assert.match(
  rightDockCss,
  /\.right-sidebar-panel\[hidden\]\s*\{\s*display:\s*none;/,
);
assert.match(
  rightDockCss,
  /\.right-sidebar\[data-resizing="true"\]\s*\{\s*transition:\s*none;/,
  "pointer resizing must disable the shell transition",
);
assert.match(
  rightDockCss,
  /\.right-sidebar\s*\{[^}]*display:\s*flex;[^}]*flex:\s*0 0 auto;/s,
  "right panel shell must occupy flex layout space",
);
assert.match(
  rightDockCss,
  /\.right-sidebar\s*\{[^}]*justify-content:\s*flex-end;/s,
  "right rail must stay anchored while the panel is hidden or resizing",
);
const narrowRightSidebarRule = rightDockCss.match(
  /@media\s*\(max-width:\s*900px\)\s*\{\s*\.right-sidebar,\s*\.right-sidebar:not\(\.collapsed\)\s*\{([^}]*)\}/,
);
assert.ok(narrowRightSidebarRule, "narrow right-sidebar rule missing");
assert.match(
  narrowRightSidebarRule[1],
  /position:\s*relative;/,
  "narrow right panel must remain a layout participant",
);
assert.doesNotMatch(
  narrowRightSidebarRule[1],
  /position:\s*fixed;/,
  "narrow right panel must not become a fixed overlay",
);

function parseTsx(text, name) {
  return ts.createSourceFile(name, text, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
}

function attr(opening, name) {
  return opening.attributes.properties.find(
    (property) => ts.isJsxAttribute(property) && property.name.text === name,
  );
}

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
for (const name of [
  "aria-label",
  "aria-orientation",
  "aria-valuemin",
  "aria-valuemax",
  "aria-valuenow",
  "tabIndex",
]) {
  assert.ok(attr(resizeSeparator, name), `separator ${name} missing`);
}
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
const lostPointerCapture = attr(resizeSeparator, "onLostPointerCapture");
assert.ok(lostPointerCapture, "separator lost-pointer cleanup missing");
assert.ok(ts.isJsxExpression(lostPointerCapture.initializer));
assert.equal(
  lostPointerCapture.initializer.expression?.getText(rightSidebarFile),
  "resetResize",
  "lost pointer capture must use the shared idempotent cleanup",
);
assert.match(
  rightSidebar,
  /function resetResize\(\)\s*\{\s*resetPanelResize\(dragRef, setResizing\);\s*\}/s,
  "React resize state must delegate to the executable cleanup",
);
assert.match(
  rightSidebar,
  /function finishResize[\s\S]*?releasePointerCapture\(event\.pointerId\);[\s\S]*?resetResize\(\);\s*\}/,
  "normal pointer release must use the same cleanup",
);

for (const viewName of ["VIEW_HISTORY", "VIEW_BOOKMARKS", "VIEW_FILES"]) {
  let railButton;
  function findRailButton(node) {
    if (
      ts.isJsxElement(node) &&
      node.openingElement.tagName.getText(rightSidebarFile) === "button" &&
      attr(node.openingElement, "data-view")?.getText(rightSidebarFile).includes(viewName)
    ) {
      railButton = node.openingElement;
      return;
    }
    ts.forEachChild(node, findRailButton);
  }
  findRailButton(rightSidebarFile);
  assert.ok(railButton, `${viewName} rail item must be a native button`);
  assert.equal(attr(railButton, "type")?.initializer?.text, "button");
  assert.ok(attr(railButton, "title"), `${viewName} rail item missing title`);
  assert.ok(attr(railButton, "aria-label"), `${viewName} rail item missing aria-label`);
  assert.ok(attr(railButton, "aria-pressed"), `${viewName} rail item missing aria-pressed`);
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

assertNoNestedButtons(manager, "bookmarks-panel.tsx");
assertNoNestedButtons(rightSidebar, "right-sidebar.tsx");

const managerFile = parseTsx(manager, "bookmarks-panel.tsx");
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
findActions(managerFile);
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
assert.equal(iconButtons.length, 5, "expected edit/save/cancel/full-tab/delete controls");
for (const button of iconButtons) {
  assert.ok(attr(button, "title"), "icon-only bookmark control missing title");
  assert.ok(attr(button, "aria-label"), "icon-only bookmark control missing aria-label");
  assert.equal(attr(button, "type")?.initializer?.text, "button");
}
for (const [label, en, zh] of [
  ["saveLabel", "Save bookmark title", "保存书签标题"],
  ["cancelLabel", "Cancel editing", "取消编辑"],
  ["editLabel", "Edit bookmark title", "编辑书签标题"],
  ["fullTabLabel", "Open in full tab", "在完整标签页中打开"],
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
