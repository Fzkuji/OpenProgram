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
// Deterministic tab-id helpers (builtinTabId, BuiltinPage, …) now live in
// their own module; the store's openBuiltinTab action still CALLS them.
const tabIdsPath = new URL("../lib/state/center-tab-ids.ts", import.meta.url);
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
const tabIds = readFileSync(tabIdsPath, "utf8");
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
// The manager is a folder TREE now: search matches a node by its own
// title/url and keeps the folders leading to a hit, rows nest by depth,
// and every folder operation is reachable from the page.
assert.match(manager, /node\.title\.toLowerCase\(\)\.includes\(needle\)/);
assert.match(manager, /node\.url\.toLowerCase\(\)\.includes\(needle\)/);
assert.match(manager, /function matchesQuery/, "search must recurse into folders");
assert.match(manager, /readBookmarkTree/, "manager must read the tree, not the flat list");
assert.match(manager, /renameNode\(id,\s*draftTitle\)/);
assert.match(manager, /deleteNode\(node\.id\)/);
assert.match(manager, /createFolder\(newFolderLabel\)/, "new-folder button missing");
assert.match(manager, /moveNode\(dragId,\s*parentId\)/, "drag-to-move missing");
assert.match(manager, /openWebTab\(node\.url\)/);
assert.match(manager, /renderChildren\(node,\s*depth \+ 1\)/, "folders must render children");
// Dropping on the list background moves a node back out to the root.
assert.match(manager, /handleDrop\(BOOKMARKS_ROOT_ID\)/);
// Between-row drop zones reorder among siblings (moveNode with an index),
// on top of the existing drop-INTO-a-folder path.
assert.match(manager, /function handleReorderDrop/, "sibling reorder handler missing");
assert.match(manager, /moveNode\(dragId,\s*parentId,\s*from >= 0 && from < index \? index - 1 : index\)/,
  "reorder must compensate for the node being spliced out before re-insertion");
assert.match(manager, /function dropZone/, "between-row drop zones missing");
assert.match(manager, /className="bookmark-drop-zone"/, "insertion line needs its own class");
assert.match(manager, /handleReorderDrop\(parentId,\s*index\)/);
// The zone marks itself with the same attribute the row highlight uses.
assert.match(manager, /data-drop-target=\{active \? "true" : undefined\}/);
// A zone before every row plus one after the last: every slot reachable.
assert.match(manager, /function renderChildren/, "sibling lists must render drop zones");
assert.match(manager, /dropZone\(parent\.id,\s*parent\.children\.length,\s*depth\)/,
  "the trailing zone (append to end) is missing");
// Zones index the unfiltered children, so they are suppressed during a
// search — otherwise a visible gap would point at the wrong slot.
assert.match(manager, /if \(!needle\) \{\n\s*out\.push\(/, "drop zones must be hidden while searching");
assert.match(manager, /if \(!needle && out\.length > 0\)/, "the trailing zone must be hidden too");
// The insertion line must be styled, not invisible.
assert.match(rightDockCss, /\.bookmark-drop-zone\b/, "drop zone has no styling");
assert.match(
  rightDockCss,
  /\.bookmark-drop-zone\[data-drop-target="true"\]/,
  "the active insertion line must be visually distinct",
);
assert.match(manager, /text\("No bookmarks yet",\s*"还没有书签"\)/);
assert.match(manager, /text\("No matching bookmarks",\s*"没有匹配的书签"\)/);
// Icons: animated-icons first, lucide static second, never emoji.
assert.match(manager, /FolderPlusIcon/, "new-folder button must use the animated icon");
assert.doesNotMatch(
  manager,
  /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u,
  "builtin-tab-pane must not use emoji",
);

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
assert.match(tabIds, /export function builtinTabId\(page: BuiltinPage\): string \{\s*return `b:\$\{page\}`;/);
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
  ["newFolderLabel", "New folder", "新建文件夹"],
  ["expandLabel", "Expand folder", "展开文件夹"],
  ["collapseLabel", "Collapse folder", "折叠文件夹"],
]) {
  assert.match(manager, new RegExp(`const ${label} = text\\("${en}",\\s*"${zh}"\\)`));
}
// Mutations go through the store + change event, never straight into
// component state (which would desync the other bookmark views).
assert.doesNotMatch(manager, /setTree\((?:renameNode|deleteNode|createFolder|moveNode)\(/);

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

/** The stored tree's bookmark rows, as {title,url} — the storage
 *  assertions care about content, not about generated ids. */
function storedBookmarks() {
  const raw = storage.get(bookmarks.BOOKMARKS_STORAGE_KEY);
  const parsed = JSON.parse(raw);
  assert.equal(parsed.version, bookmarks.BOOKMARKS_VERSION, "stored tree must carry a version");
  return bookmarks.flattenBookmarks(parsed.root);
}

assert.deepEqual(bookmarks.readBookmarks(), []);
storage.set(bookmarks.BOOKMARKS_STORAGE_KEY, "not-json");
assert.deepEqual(bookmarks.readBookmarks(), []);
assert.deepEqual(bookmarks.toggleBookmark(first), [first]);
assert.deepEqual(storedBookmarks(), [first]);
assert.equal(changes, 1);
assert.deepEqual(bookmarks.toggleBookmark(first), []);
assert.deepEqual(storedBookmarks(), []);

storage.set(bookmarks.BOOKMARKS_STORAGE_KEY, "[");
assert.deepEqual(bookmarks.toggleBookmark(first), [first]);
assert.deepEqual(bookmarks.toggleBookmark(second), [first, second]);
assert.deepEqual(storedBookmarks(), [first, second]);
assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
assert.equal(changes, 4);

assert.deepEqual(bookmarks.removeBookmark(first.url), [second]);
assert.deepEqual(storedBookmarks(), [second]);
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
assert.deepEqual(storedBookmarks(), [{ title: second.url, url: second.url }]);
assert.doesNotThrow(() => bookmarks.toggleBookmark(first));
assert.deepEqual(bookmarks.toggleBookmark(first), [{ title: second.url, url: second.url }]);
assert.deepEqual(storedBookmarks(), [{ title: second.url, url: second.url }]);
assert.equal(changes, 7);
failWrites = false;

// ---- Migration: the v1 flat array becomes the root folder -----------
// Nobody may lose bookmarks on upgrade, so the legacy shape is read as
// a tree without any explicit migration step.
storage.set(bookmarks.BOOKMARKS_STORAGE_KEY, JSON.stringify([first, second]));
const migrated = bookmarks.readBookmarkTree();
assert.equal(migrated.kind, "folder");
assert.equal(migrated.id, bookmarks.BOOKMARKS_ROOT_ID);
assert.equal(migrated.children.length, 2);
assert.ok(
  migrated.children.every((node) => node.kind === "bookmark" && typeof node.id === "string"),
  "migrated rows must become identified bookmark nodes",
);
assert.deepEqual(bookmarks.flattenBookmarks(migrated), [first, second]);
assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
// Junk entries in the legacy array are dropped, the good ones survive.
storage.set(
  bookmarks.BOOKMARKS_STORAGE_KEY,
  JSON.stringify([first, { title: "no url" }, null, "nope", second]),
);
assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
// A v2 tree round-trips unchanged.
bookmarks.toggleBookmark({ title: "Kept", url: "https://kept.example/" });
assert.deepEqual(bookmarks.readBookmarks(), [
  first,
  second,
  { title: "Kept", url: "https://kept.example/" },
]);

// ---- Folder operations ----------------------------------------------
storage.set(bookmarks.BOOKMARKS_STORAGE_KEY, JSON.stringify([first, second]));
changes = 0;

const withFolder = bookmarks.createFolder("  Work  ");
assert.equal(changes, 1);
const work = withFolder.children.find((node) => node.kind === "folder");
assert.ok(work, "createFolder must add a folder node");
assert.equal(work.title, "Work", "folder title is trimmed");
assert.deepEqual(work.children, []);
// A blank name still yields a usable folder rather than an empty row.
assert.ok(
  bookmarks.createFolder("   ").children.some((node) => node.title === "New folder"),
  "blank folder names fall back to a default",
);
// Creating inside a folder that does not exist is a no-op.
changes = 0;
bookmarks.createFolder("Nowhere", "missing-id");
assert.equal(changes, 0, "createFolder into a missing parent must not write");

// Move a bookmark into the folder: the flat list keeps it, the tree
// nests it.
const firstId = bookmarks
  .readBookmarkTree()
  .children.find((node) => node.kind === "bookmark" && node.url === first.url).id;
bookmarks.moveNode(firstId, work.id);
let tree = bookmarks.readBookmarkTree();
let workNow = bookmarks.findNode(tree, work.id);
assert.equal(workNow.children.length, 1, "moved bookmark must land in the folder");
assert.equal(workNow.children[0].url, first.url);
assert.ok(
  !tree.children.some((node) => node.kind === "bookmark" && node.url === first.url),
  "the moved bookmark must leave its old parent",
);
assert.deepEqual(
  bookmarks.readBookmarks().map((bookmark) => bookmark.url).sort(),
  [first.url, second.url].sort(),
  "moving must not lose a bookmark from the flat view",
);

// Reorder / move back out to the root at an explicit index.
bookmarks.moveNode(firstId, bookmarks.BOOKMARKS_ROOT_ID, 0);
tree = bookmarks.readBookmarkTree();
assert.equal(tree.children[0].url, first.url, "index 0 must place the node first");
assert.equal(bookmarks.findNode(tree, work.id).children.length, 0);

// Sibling reordering: dragging a row between two others. The node is
// spliced out before re-insertion, so a downward move targets index-1 —
// the same arithmetic handleReorderDrop() does in the UI.
const reorderRoot = bookmarks.readBookmarkTree();
const reorderIds = reorderRoot.children.map((node) => node.id);
assert.ok(reorderIds.length >= 3, "need at least three siblings to test reordering");
const [topId, midId] = reorderIds;
// Move the first sibling down past the second (drop zone index 2).
const fromIndex = 0;
bookmarks.moveNode(topId, bookmarks.BOOKMARKS_ROOT_ID, fromIndex < 2 ? 1 : 2);
assert.deepEqual(
  bookmarks.readBookmarkTree().children.slice(0, 2).map((node) => node.id),
  [midId, topId],
  "dropping between the 2nd and 3rd row must reorder the siblings",
);
// And back up to the top (upward move needs no compensation).
bookmarks.moveNode(topId, bookmarks.BOOKMARKS_ROOT_ID, 0);
assert.equal(
  bookmarks.readBookmarkTree().children[0].id,
  topId,
  "dropping on the leading zone must move the node back to the top",
);
// Reordering must not lose or duplicate anything.
assert.deepEqual(
  bookmarks.readBookmarkTree().children.map((node) => node.id).sort(),
  [...reorderIds].sort(),
  "reordering must preserve the sibling set exactly",
);

// Renaming works on folders and bookmarks alike.
bookmarks.renameNode(work.id, "  Reading  ");
assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), work.id).title, "Reading");
// A blank folder rename keeps the old name; a blank bookmark rename
// falls back to the url (same rule as renameBookmark).
bookmarks.renameNode(work.id, "   ");
assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), work.id).title, "Reading");
bookmarks.renameNode(firstId, "   ");
assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), firstId).title, first.url);
// The root and unknown ids are not renameable.
changes = 0;
bookmarks.renameNode(bookmarks.BOOKMARKS_ROOT_ID, "Root");
bookmarks.renameNode("missing-id", "Nope");
assert.equal(changes, 0, "renaming the root or a missing node must not write");

// A folder must never be moved into its own subtree — that would
// detach every bookmark under it from the root.
const nested = bookmarks.createFolder("Nested", work.id);
const nestedId = bookmarks.findNode(nested, work.id).children[0].id;
changes = 0;
bookmarks.moveNode(work.id, nestedId);
assert.equal(changes, 0, "moving a folder into its own descendant must be refused");
bookmarks.moveNode(work.id, work.id);
assert.equal(changes, 0, "moving a folder into itself must be refused");
bookmarks.moveNode(bookmarks.BOOKMARKS_ROOT_ID, work.id);
assert.equal(changes, 0, "the root is not movable");
bookmarks.moveNode("missing-id", work.id);
assert.equal(changes, 0, "moving a missing node must not write");
bookmarks.moveNode(firstId, "missing-parent");
assert.equal(changes, 0, "moving into a missing parent must not write");
assert.ok(bookmarks.findNode(bookmarks.readBookmarkTree(), nestedId), "subtree survived");

// Deleting a folder takes its whole subtree with it.
bookmarks.moveNode(firstId, nestedId);
assert.deepEqual(bookmarks.readBookmarks().map((bookmark) => bookmark.url).sort(), [
  first.url,
  second.url,
].sort());
bookmarks.deleteNode(work.id);
tree = bookmarks.readBookmarkTree();
assert.equal(bookmarks.findNode(tree, work.id), null, "deleted folder is gone");
assert.equal(bookmarks.findNode(tree, nestedId), null, "nested folder went with it");
assert.deepEqual(
  bookmarks.readBookmarks(),
  [second],
  "the bookmarks under a deleted folder go with it",
);
// The root itself is not deletable.
changes = 0;
bookmarks.deleteNode(bookmarks.BOOKMARKS_ROOT_ID);
bookmarks.deleteNode("missing-id");
assert.equal(changes, 0, "deleting the root or a missing node must not write");
assert.deepEqual(bookmarks.readBookmarks(), [second]);

// Folder operations survive a storage failure without throwing.
failWrites = true;
assert.doesNotThrow(() => bookmarks.createFolder("Doomed"));
assert.doesNotThrow(() => bookmarks.deleteNode(firstId));
assert.equal(changes, 0);
failWrites = false;

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
