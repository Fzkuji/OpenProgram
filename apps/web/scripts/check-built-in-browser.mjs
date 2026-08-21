import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import postcss from "postcss";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const launcher = read("../components/center-tabs/new-tab-page.tsx");
const browserHome = read("../components/center-tabs/browser-home-page.tsx");
const browserControls = read("../components/center-tabs/browser-controls.tsx");
const browserGlyph = read("../components/center-tabs/browser-glyph.tsx");
const browserPrefs = read("../lib/browser-prefs.ts");
const browserLayoutSource = read("../lib/browser-layout.ts");
const webTabPane = read("../components/center-tabs/web-tab-pane.tsx");
const mainMenu = read("../components/center-tabs/main-menu.tsx");
const desktopMainMenu = read("../app/menu-overlay/main-menu/page.tsx");
const contextMenu = read("../app/menu-overlay/context-menu/page.tsx");
const browserSettings = read("../components/settings/browser-settings.tsx");
const settingsLayout = read("../components/settings/settings-tabs-layout.tsx");
const browserSettingsRoute = read("../app/(shell)/settings/browser/page.tsx");
const centerTabsCss = read("../components/center-tabs/center-tabs.module.css");
const cssRoot = postcss.parse(centerTabsCss);
const historyCss = read("../app/styles/right-dock/web-history.css");
const dropdownMenu = read("../components/ui/dropdown-menu.tsx");
const builtin = read("../components/center-tabs/builtin-tab-pane.tsx");
const bridge = read("../lib/desktop-bridge.ts");
const bridgeTypes = read("../lib/desktop-bridge-types.ts");
const preload = read("../../desktop/preload.js");
const main = read("../../desktop/main.js");
const historyGroupsSource = read("../lib/history-groups.ts");

for (const label of ["Files", "Browser", "Terminal"]) {
  assert.match(launcher, new RegExp(`text\\(\\"${label}\\"`));
}
assert.match(
  launcher,
  /<button[^>]*onClick=\{openNewChat\}>[\s\S]*?text\("New chat", "新建对话"\)[\s\S]*?<\/button>/,
);
assert.match(
  launcher,
  /function openNewChat\(\) \{[\s\S]*?claimDraftSessionTab\(\);[\s\S]*?newSession\(draftId\);[\s\S]*?\}/,
);
assert.doesNotMatch(launcher, /Side chat|侧边聊天/);
assert.doesNotMatch(launcher, /Claude Code|openBuiltinTab\("claude"\)/);
assert.doesNotMatch(launcher, /readBookmarks|readShortcuts|ntpUrlInput/);
assert.match(browserHome, /BrowserImportDialog/);
assert.match(browserHome, /importBookmarkTree/);
assert.match(browserHome, /markBrowserImportPromptFinished/);
assert.match(browserHome, /api\.cancel\(requestId\)/);
assert.match(browserHome, /text\("Cancel", "取消"\)/);
assert.match(browserHome, /consumeBrowserImportRequest/);
assert.match(browserHome, /canImport && showImport/);
assert.doesNotMatch(browserHome, /browser-import-dismissed/);
assert.doesNotMatch(browserHome, /readBookmarks|removeBookmark|subscribeBookmarks|ntpBookmarks|ntpBookmark/);
assert.match(browserHome, /readShortcuts/);
assert.match(browserHome, /className=\{styles\.webToolbar\}/);
assert.match(browserHome, /<BookmarkBar ownerId=\{menuOwnerId\} onNavigate=\{go\}/);
assert.match(browserHome, /<BrowserMenu[\s\S]*ownerId=\{menuOwnerId\}/);
assert.match(browserHome, /canGoHome=\{false\}/);
assert.match(browserHome, /canOpenExternal=\{false\}/);
assert.match(browserHome, /<BookmarksLibraryButton \/>/);
assert.match(browserHome, /ArrowLeft[\s\S]*ArrowRight[\s\S]*RotateCw[\s\S]*House/);
for (const label of ["Back", "Forward", "Reload", "Home", "Bookmark", "Open in browser"]) {
  assert.match(
    browserHome,
    new RegExp(`<button[^>]*disabled[^>]*title=\\{text\\("${label}"`),
    `Browser home ${label} must remain visible but disabled`,
  );
}
assert.match(browserHome, /actions=\{\{ home: \(\) => \{\}, forward: \(\) => \{\}, openExternal: \(\) => \{\} \}\}/);
assert.doesNotMatch(browserHome, /browserHomeToolbar|text\("Open", "打开"\)/);
assert.match(browserGlyph, /BrowserGlyph/);
assert.match(browserHome, /<BrowserGlyph/);
assert.equal(
  launcher.match(/className=\{styles\.ntpGlyph\}/g)?.length,
  3,
  "the three non-browser launchers must use the same icon container as Browser",
);
function finalTopLevelDecl(selector, property) {
  let value;
  for (const node of cssRoot.nodes) {
    if (node.type !== "rule" || !node.selectors?.includes(selector)) continue;
    node.walkDecls(property, (declaration) => { value = declaration.value; });
  }
  return value;
}
for (const [tone, icon, primary, secondary] of [
  ["files", "FileText", "var(--meter-fill)", "var(--accent-cyan)"],
  ["chat", "MessageCirclePlus", "var(--accent-green)", "var(--accent-cyan)"],
  ["terminal", "TerminalSquare", "var(--accent-purple)", "var(--meter-fill)"],
]) {
  assert.match(
    launcher,
    new RegExp(`data-tone="${tone}"[^>]*>[\\s\\S]*?<${icon} size=\\{11\\} strokeWidth=\\{2\\.1\\}`),
  );
  const selector = `.ntpGlyph[data-tone="${tone}"]`;
  assert.equal(finalTopLevelDecl(selector, "--glyph-primary"), primary);
  assert.equal(finalTopLevelDecl(selector, "--glyph-secondary"), secondary);
}
assert.match(launcher, /<BrowserGlyph size=\{18\} \/>/);
assert.equal(finalTopLevelDecl(".ntpGlyph", "width"), "18px");
assert.equal(finalTopLevelDecl(".ntpGlyph", "height"), "18px");
assert.equal(finalTopLevelDecl(".browserGlyph", "--glyph-primary"), "var(--accent-blue)");
assert.equal(finalTopLevelDecl(".browserGlyph", "--glyph-secondary"), "#8b5cf6");
for (const selector of [".browserGlyph", ".ntpGlyph"]) {
  const background = finalTopLevelDecl(selector, "background");
  assert.match(background, /radial-gradient\(/);
  assert.match(background, /linear-gradient\(/);
}
assert.match(browserPrefs, /SHOW_BOOKMARKS_BAR_KEY/);
assert.match(browserPrefs, /BROWSER_IMPORT_PROMPT_FINISHED_KEY/);
assert.match(browserControls, /function BrowserMenu/);
assert.match(browserControls, /canGoHome = true/);
assert.match(browserControls, /canOpenExternal = true/);
assert.match(browserControls, /item\("home", "Home", "主页", \{ disabled: !canGoHome \}\)/);
assert.match(browserControls, /item\("open-external", "Open in browser", "在浏览器中打开", \{ disabled: !canOpenExternal \}\)/);
assert.match(browserControls, /row\("home", <House size=\{14\} \/>, "Home", "主页", false, !canGoHome\)/);
assert.match(browserControls, /row\("open-external", <ExternalLink size=\{14\} \/>, "Open in browser", "在浏览器中打开", false, !canOpenExternal\)/);
assert.match(browserControls, /function BookmarkBar/);
assert.match(browserControls, /bookmarkBarLayout\(tree\)/);
assert.doesNotMatch(browserControls, /setNodes\(readBookmarkTree\(\)\.children\)/);
assert.match(browserControls, /bookmarkOverflowFolder/);
assert.match(browserControls, /trailingFolders\.map/);
assert.match(browserControls, /new ResizeObserver\(updateOverflow\)/);
assert.match(browserControls, /items\.slice\(overflowStart\)/);
assert.match(browserControls, /styles\.bookmarkBarMoreSlot/);
assert.match(browserControls, /\[items\.length, tree, visible\]/);
assert.match(webTabPane, /const menuOwnerId = useId\(\)/);
assert.match(browserControls, /browserActionPrefix\(ownerId\)/);
assert.match(browserControls, /bookmarkFolderActionPrefix\(ownerId, folder\.id\)/);
assert.match(
  browserControls,
  /iconUrl:\s*node\.faviconUrl\s*\|\|\s*faviconUrl\(node\.url\)/,
  "desktop bookmark-folder payloads must include each website favicon",
);
assert.match(browserControls, /icon:\s*"folder"/);
assert.match(
  browserControls,
  /onMouseEnter=\{\(event\) => \{[\s\S]*?mainMenu\.cancelClose\?\.\(\);[\s\S]*?openFolderMenu\(event\.currentTarget\);[\s\S]*?\}\}/,
);
assert.match(browserControls, /cascade:\s*true/);
const browserMenuOwners = [...webTabPane.matchAll(/<BrowserMenu[\s\S]*?ownerId=\{([^}]+)\}/g)]
  .map((match) => match[1]);
const bookmarkBarOwners = [...webTabPane.matchAll(/<BookmarkBar\s+ownerId=\{([^}]+)\}/g)]
  .map((match) => match[1]);
assert.deepEqual(browserMenuOwners, ["menuOwnerId", "menuOwnerId"]);
assert.deepEqual(bookmarkBarOwners, ["menuOwnerId", "menuOwnerId"]);
assert.doesNotMatch(webTabPane, /<(?:BrowserMenu|BookmarkBar)[^>]*ownerId=\{tabId\}/);
assert.match(browserControls, /anchor: \{ right: rect\.right, y: rect\.bottom \+ 4, align: "end"/);
assert.doesNotMatch(webTabPane, /SplitButton|Open split view|Columns2/);
assert.match(webTabPane, /goBack\(tabId\)[\s\S]*className=\{`\$\{styles\.webToolbarBtn\} \$\{styles\.webToolbarForward\}`\}[\s\S]*goForward\(tabId\)/);
assert.match(webTabPane, /function BookmarkButton[\s\S]*className=\{styles\.webToolbarBtn\}/);
assert.match(webTabPane, /function HomeButton[\s\S]*styles\.webToolbarMedium/);
assert.match(webTabPane, /<BookmarkBar ownerId=\{menuOwnerId\}/);
assert.match(browserControls, /Show bookmarks bar/);
assert.match(browserControls, /Clear browsing data/);
assert.match(browserControls, /Browser settings/);
assert.match(browserControls, /case "collapse-to-pip":/);
assert.match(browserControls, /Collapse to floating window/);
assert.match(browserControls, /openBuiltinTab\("bookmarks"\)/);
assert.match(browserControls, /openBuiltinTab\("history"\)/);
assert.match(browserControls, /openBuiltinTab\("downloads"\)/);
assert.match(browserControls, /row\("downloads", <Download/);
assert.match(browserControls, /requestBrowserImport/);
assert.match(browserControls, /disabled: !canImport/);
assert.match(browserControls, /separatorBefore/);
assert.match(browserControls, /checked/);
assert.match(browserSettings, /BrowserImportDialog/);
assert.match(browserSettings, /bridge\?\.browserImport \? \(/);
assert.doesNotMatch(browserSettings, /disabled=\{!bridge\?\.browserImport\}/);
assert.match(browserSettings, /browserData\.clear/);
assert.match(browserSettings, /<h2 className=\{styles\.pageTitle\}/);
assert.doesNotMatch(browserSettings, /<h1\b/);
assert.match(browserSettings, /styles\.systemRow/);
assert.match(browserSettings, /<Switch/);
assert.doesNotMatch(browserSettings, /<input type="checkbox"/);
assert.match(settingsLayout, /\/settings\/browser/);
assert.match(browserSettingsRoute, /<BrowserSettings/);
assert.match(contextMenu, /item\.checked/);
assert.match(contextMenu, /item\.iconUrl/);
assert.match(contextMenu, /item\.icon === "folder"/);
assert.match(contextMenu, /params\.get\("cascade"\) === "1"/);
assert.match(contextMenu, /mainMenuBridge\(\)\?\.onUpdate\?\./);
assert.match(contextMenu, /onError=.*setBroken/);
assert.match(bridgeTypes, /iconUrl\?: string/);
assert.match(bridgeTypes, /icon\?: "folder"/);
assert.match(bridgeTypes, /cascade\?: boolean/);
assert.match(bridgeTypes, /onUpdate\?\(cb:/);
assert.match(preload, /ipcRenderer\.on\("main-menu:update"/);
assert.match(main, /ctx\.mainMenuCascade/);
assert.match(main, /webContents\.send\("main-menu:update"/);
const nestedMenuItems = contextMenu.slice(
  contextMenu.indexOf("function NestedMenuItems"),
  contextMenu.indexOf("function NestedContextMenu"),
);
assert.equal(
  nestedMenuItems.match(/<ItemIcon item=\{item\} \/>/g)?.length,
  2,
  "nested folder triggers and website rows must both render their icon slot",
);
assert.match(nestedMenuItems, /<DropdownMenuPrimitive\.SubTrigger[\s\S]*?<ItemIcon item=\{item\} \/>/);
assert.match(nestedMenuItems, /<DropdownMenuPrimitive\.Item[\s\S]*?<ItemIcon item=\{item\} \/>/);
const flatMenuItems = contextMenu.slice(contextMenu.indexOf("function ContextMenuOverlayPage"));
assert.match(flatMenuItems, /role="menuitemcheckbox"[\s\S]*?<ItemIcon item=\{item\} \/>/);
assert.equal(
  contextMenu.match(/<ItemIcon item=\{item\} \/>/g)?.length,
  3,
  "desktop bookmark favicons must render in nested triggers, nested rows, and flat rows",
);
const bookmarkMenuNodes = browserControls.slice(
  browserControls.indexOf("function BookmarkMenuNodes"),
  browserControls.indexOf("function BookmarkFavicon"),
);
assert.match(
  bookmarkMenuNodes,
  /<DropdownMenuItem[\s\S]*?<BookmarkFavicon node=\{node\} \/>/,
  "the Web fallback website row must render its favicon",
);
const iconPayload = [{
  id: "bookmark:1",
  label: "Signed favicon",
  iconUrl: "https://example.test/favicon.ico?token=a+b&next=x%2Fy#fragment",
}];
const encodedIconPayload = new URLSearchParams({ items: JSON.stringify(iconPayload) }).toString();
assert.deepEqual(
  JSON.parse(new URLSearchParams(encodedIconPayload).get("items")),
  iconPayload,
  "context-menu URL serialization must preserve complete favicon URLs",
);
assert.match(contextMenu, /item\.separatorBefore/);
assert.match(contextMenu, /maxHeight:\s*"calc\(100vh - 48px\)"/);
assert.match(contextMenu, /overflowY:\s*"auto"/);
assert.match(
  contextMenu,
  /width:\s*Math\.max\(rect\.width, panel\.scrollWidth\)/,
  "the overlay must report its intrinsic width instead of a clipped viewport width",
);
assert.match(
  contextMenu,
  /height:\s*Math\.max\(rect\.height, panel\.scrollHeight\)/,
  "the overlay must report its intrinsic height instead of recursively shrinking to its own viewport",
);
assert.match(main, /Math\.min\(panelH,\s*Math\.max\(1,\s*anchor\.winH - 16 \* zoom\)\)/);
assert.match(centerTabsCss, /container-type:\s*inline-size/);
assert.match(centerTabsCss, /@container\s*\(max-width:\s*719px\)/);
assert.match(centerTabsCss, /@container\s*\(max-width:\s*559px\)/);
assert.match(centerTabsCss, /@container\s*\(max-width:\s*519px\)/);
const topLevelSelectors = new Set(
  cssRoot.nodes
    .filter((node) => node.type === "rule")
    .flatMap((node) => node.selectors ?? []),
);
for (const selector of [".builtinHeaderActions", ".filesPage", ".ntpLauncher", ".ntpTileInitial", ".browserHome"]) {
  assert.ok(topLevelSelectors.has(selector), `${selector} must remain a top-level CSS rule`);
}
assert.match(historyCss, /\.browsing-history-row\s*\{[^}]*min-height:\s*38px/s);
assert.doesNotMatch(mainMenu, /openBuiltinTab\("bookmarks"\)|openBuiltinTab\("history"\)/);
assert.doesNotMatch(desktopMainMenu, /"bookmarks"|"history"/);
assert.match(mainMenu, /getBoundingClientRect\(\)/);
assert.match(mainMenu, /window\.innerWidth\s*-\s*trigger\.right/);
assert.match(mainMenu, /top:\s*Math\.round\(trigger\.bottom\s*\+\s*4\)/);
assert.doesNotMatch(mainMenu, /rightInset:\s*8/);
assert.match(dropdownMenu, /align\s*=\s*"end"/);
assert.match(dropdownMenu, /sideOffset\s*=\s*4/);
assert.match(builtin, /groupHistoryByLocalDate/);
assert.match(builtin, /browsing-history-row/);
assert.match(builtin, /function DownloadsPage\(\)/);
assert.match(builtin, /api\.onChanged\(refresh\)/);
assert.match(builtin, /api\.open\(entry\.id\)/);
assert.match(builtin, /api\.show\(entry\.id\)/);
assert.match(builtin, /api\.cancel\(entry\.id\)/);
assert.match(builtin, /entry\.active \? \(/);
assert.match(builtin, /entries\?\.some\(\(entry\) => !entry\.active\)/);
assert.match(bridge, /downloads\?: DesktopDownloadsApi/);
assert.match(bridge, /browserImport\?: DesktopBrowserImportApi/);
assert.match(bridge, /browserData\?: DesktopBrowserDataApi/);
assert.match(bridgeTypes, /cancel\?\(requestId: string\): Promise<boolean>/);
assert.match(bridgeTypes, /stop\(id: string\): void/);
assert.match(bridgeTypes, /find\?\(\s*id: string,\s*query: string/s);
assert.match(bridgeTypes, /stopFind\?\(/);
assert.match(bridgeTypes, /zoom\?\(id: string, action: "in" \| "out" \| "reset"\)/);
assert.match(bridgeTypes, /print\?\(id: string\): Promise<boolean>/);
assert.match(bridgeTypes, /capture\?\(id: string\): Promise<string \| null>/);
assert.match(bridgeTypes, /setPipZoom\?\(id: string, width: number \| null\): void/);
assert.match(webTabPane, /className=\{styles\.webFindBar\}/);
assert.match(webTabPane, /const canFind = typeof bridge\.webTab\.find === "function"/);
assert.match(webTabPane, /bridge\.webTab\.stopFind\?\./);
assert.match(browserControls, /case "find":/);
assert.match(browserControls, /case "zoom-in":/);
assert.match(browserControls, /case "print":/);
assert.match(preload, /browser-import:list-sources/);
assert.match(preload, /browser-import:cancel/);
assert.match(preload, /browser-data:clear/);
assert.match(preload, /downloads:changed/);
assert.match(preload, /webtab:stop/);
assert.match(preload, /webtab:capture/);
assert.match(preload, /webtab:set-pip-zoom/);
assert.match(main, /browser-import:run/);
assert.match(main, /browser-import:cancel/);
assert.match(main, /new AbortController\(\)/);
assert.match(main, /browser-data:clear/);
assert.match(main, /targetSession\.on\("will-download"/);
assert.match(main, /downloads:open/);
assert.match(main, /downloads:show/);
assert.match(main, /webtab:stop/);
assert.match(main, /webtab:capture/);
assert.match(main, /capturePage\(\)/);
assert.match(main, /webtab:set-pip-zoom/);
assert.match(main, /const PIP_VIRTUAL_WIDTH = 1280/);
assert.match(main, /cookies:\s*result\.cookies/);
assert.doesNotMatch(main, /cookies:\s*result\.(?:cookies\.)?(?:name|value)/);

const browserLayoutCompiled = ts.transpileModule(browserLayoutSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const browserLayout = await import(
  `data:text/javascript;base64,${Buffer.from(browserLayoutCompiled).toString("base64")}`
);
assert.deepEqual(browserLayout.browserResponsiveMenuItems(720, { forward: true }), {
  home: false,
  forward: false,
  openExternal: false,
});
assert.deepEqual(browserLayout.browserResponsiveMenuItems(600, { forward: true }), {
  home: true,
  forward: false,
  openExternal: true,
});
for (const [key, action] of [
  ["f", "find"],
  ["+", "zoom-in"],
  ["=", "zoom-in"],
  ["-", "zoom-out"],
  ["0", "reset-zoom"],
  ["p", "print"],
]) {
  assert.equal(
    browserLayout.browserPageShortcut({ key, metaKey: true, ctrlKey: false }),
    action,
  );
  assert.equal(
    browserLayout.browserPageShortcut({ key, metaKey: false, ctrlKey: true }),
    action,
  );
}
assert.equal(
  browserLayout.browserPageShortcut({ key: "f", metaKey: false, ctrlKey: false }),
  null,
);
assert.match(webTabPane, /onKeyDownCapture=\{handleRendererShortcut\}/);
assert.deepEqual(browserLayout.browserResponsiveMenuItems(500, { forward: true }), {
  home: true,
  forward: true,
  openExternal: true,
});
assert.deepEqual(browserLayout.browserResponsiveMenuItems(500, { forward: false }), {
  home: true,
  forward: false,
  openExternal: true,
});
const sensitivePane = {
  tabId: "w:https://example.test/path?token=SECRET#fragment",
  menuOwnerId: "pane-a",
};
const browserOwnerA = browserLayout.browserActionPrefix(sensitivePane.menuOwnerId);
const browserOwnerB = browserLayout.browserActionPrefix("pane-b");
assert.equal(browserOwnerA.includes(sensitivePane.tabId), false);
assert.equal(browserLayout.ownedActionId(`${browserOwnerA}history`, browserOwnerA), "history");
assert.equal(browserLayout.ownedActionId(`${browserOwnerB}history`, browserOwnerA), null);
const folderOwnerA = browserLayout.bookmarkFolderActionPrefix(sensitivePane.menuOwnerId, "folder-1");
const folderOwnerB = browserLayout.bookmarkFolderActionPrefix("pane-b", "folder-1");
const folderOwnerOtherRoot = browserLayout.bookmarkFolderActionPrefix(sensitivePane.menuOwnerId, "folder-2");
assert.equal(folderOwnerA.includes(sensitivePane.tabId), false);
assert.equal(
  browserLayout.ownedActionId(`${folderOwnerA}bookmark:node-7`, folderOwnerA),
  "bookmark:node-7",
);
assert.equal(browserLayout.ownedActionId(`${folderOwnerA}folder:node-8`, folderOwnerA), "folder:node-8");
assert.equal(browserLayout.ownedActionId(`${folderOwnerA}empty:node-9`, folderOwnerA), "empty:node-9");
assert.equal(browserLayout.ownedActionId(`${folderOwnerB}bookmark:node-7`, folderOwnerA), null);
assert.equal(browserLayout.ownedActionId(`${folderOwnerOtherRoot}bookmark:node-7`, folderOwnerA), null);

const historyGroupsCompiled = ts.transpileModule(historyGroupsSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const historyGroups = await import(
  `data:text/javascript;base64,${Buffer.from(historyGroupsCompiled).toString("base64")}`
);
const day = 86_400_000;
const grouped = historyGroups.groupHistoryByLocalDate([
  { id: "new-a", visitedAt: Date.UTC(2026, 7, 15, 12) },
  { id: "new-b", visitedAt: Date.UTC(2026, 7, 15, 8) },
  { id: "old", visitedAt: Date.UTC(2026, 7, 15, 8) - day },
  { id: "invalid", visitedAt: Number.NaN },
]);
assert.equal(grouped.length, 2);
assert.deepEqual(grouped.map((group) => group.entries.map((entry) => entry.id)), [["new-a", "new-b"], ["old"]]);

const browserPrefsCompiled = ts.transpileModule(browserPrefs, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const prefs = await import(
  `data:text/javascript;base64,${Buffer.from(browserPrefsCompiled).toString("base64")}`
);
const storage = () => {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
};
const oldWindow = globalThis.window;
const oldLocalStorage = globalThis.localStorage;
const oldSessionStorage = globalThis.sessionStorage;
globalThis.window = new EventTarget();
globalThis.localStorage = storage();
globalThis.sessionStorage = storage();
assert.equal(prefs.showBookmarksBar(), true);
prefs.setShowBookmarksBar(false);
assert.equal(prefs.showBookmarksBar(), false);
prefs.setShowBookmarksBar(true);
assert.equal(prefs.showBookmarksBar(), true);
assert.equal(prefs.browserImportPromptFinished(), false);
prefs.markBrowserImportPromptFinished();
assert.equal(prefs.browserImportPromptFinished(), true);
prefs.requestBrowserImport();
assert.equal(prefs.consumeBrowserImportRequest(), true);
assert.equal(prefs.consumeBrowserImportRequest(), false);
globalThis.window = oldWindow;
globalThis.localStorage = oldLocalStorage;
globalThis.sessionStorage = oldSessionStorage;

execFileSync(process.execPath, [fileURLToPath(new URL("../../desktop/browser-profile-import.js", import.meta.url))], {
  stdio: "pipe",
});

console.log("built-in browser source check passed");
