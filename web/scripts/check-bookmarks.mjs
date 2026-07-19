import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import ts from "typescript";

const sourcePath = new URL("../lib/bookmarks.ts", import.meta.url);
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
assert.match(manager, /openWebTabInSplit\(url\)/);
assert.match(manager, /openWebTab\(bookmark\.url\)/);
assert.match(manager, /text\("Open in full tab",\s*"在完整标签页中打开"\)/);
assert.match(manager, /text\("No bookmarks yet",\s*"还没有书签"\)/);
assert.match(manager, /text\("No matching bookmarks",\s*"没有匹配的书签"\)/);
assert.match(manager, /aria-label=\{[^}]+\}/);
assert.match(manager, /title=\{[^}]+\}/);
assert.match(rightSidebar, /const VIEW_BOOKMARKS = "bookmarks";/);
assert.match(rightSidebar, /data-view=\{VIEW_BOOKMARKS\}/);
assert.match(rightSidebar, /<BookmarksPanel \/>/);
assert.match(
  rightDockCss,
  /\.right-sidebar\[data-view="bookmarks"\]\s+\.right-view\[data-view="bookmarks"\]\s*\{\s*display:\s*flex;/,
);
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
