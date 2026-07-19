import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import ts from "typescript";

const sourcePath = new URL("../lib/bookmarks.ts", import.meta.url);
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
  "node --no-warnings scripts/check-bookmarks.mjs",
);
assert.match(packageJson.scripts?.check || "", /check:bookmarks/);

const source = readFileSync(sourcePath, "utf8");
const webTab = readFileSync(webTabPath, "utf8");
const newTab = readFileSync(newTabPath, "utf8");
const manager = readFileSync(managerPath, "utf8");
const rightSidebar = readFileSync(rightSidebarPath, "utf8");
const rightDockCss = readFileSync(rightDockCssPath, "utf8");
assert.match(webTab, /function BookmarkButton/);
assert.match(webTab, /toggleBookmark\(\{ url, title \}\)/);
assert.match(webTab, /<BookmarkButton url=\{effectiveUrl\} title=\{title \|\| effectiveUrl\} \/>/);
assert.match(newTab, /readBookmarks/);
assert.match(newTab, /removeBookmark/);
assert.match(manager, /addEventListener\(BOOKMARKS_CHANGE_EVENT,\s*refresh\)/);
assert.match(manager, /removeEventListener\(BOOKMARKS_CHANGE_EVENT,\s*refresh\)/);
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

const navMarker = rightSidebar.indexOf("data-view={VIEW_BOOKMARKS}");
assert.ok(navMarker >= 0, "bookmarks nav missing");
const navTagStart = rightSidebar.lastIndexOf("<", navMarker);
const navTagClose = "\n        >";
const navTagEnd = rightSidebar.indexOf(navTagClose, navMarker);
const navOpeningTag = rightSidebar.slice(navTagStart, navTagEnd + navTagClose.length);
assert.match(navOpeningTag, /^<button\b/, "bookmarks nav must be a native button");
assert.match(navOpeningTag, /type="button"/);
assert.match(navOpeningTag, /title=\{text\("Bookmarks",\s*"书签"\)\}/);
assert.match(navOpeningTag, /aria-label=\{text\("Bookmarks",\s*"书签"\)\}/);

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

console.log("bookmark storage checks passed");
