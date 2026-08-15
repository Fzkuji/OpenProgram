import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const launcher = read("../components/center-tabs/new-tab-page.tsx");
const browserHome = read("../components/center-tabs/browser-home-page.tsx");
const browserControls = read("../components/center-tabs/browser-controls.tsx");
const browserGlyph = read("../components/center-tabs/browser-glyph.tsx");
const browserPrefs = read("../lib/browser-prefs.ts");
const webTabPane = read("../components/center-tabs/web-tab-pane.tsx");
const mainMenu = read("../components/center-tabs/main-menu.tsx");
const desktopMainMenu = read("../app/menu-overlay/main-menu/page.tsx");
const contextMenu = read("../app/menu-overlay/context-menu/page.tsx");
const browserSettings = read("../components/settings/browser-settings.tsx");
const settingsLayout = read("../components/settings/settings-tabs-layout.tsx");
const browserSettingsRoute = read("../app/(shell)/settings/browser/page.tsx");
const centerTabsCss = read("../components/center-tabs/center-tabs.module.css");
const historyCss = read("../app/styles/right-dock/web-history.css");
const dropdownMenu = read("../components/ui/dropdown-menu.tsx");
const builtin = read("../components/center-tabs/builtin-tab-pane.tsx");
const bridge = read("../lib/desktop-bridge.ts");
const preload = read("../../desktop/preload.js");
const main = read("../../desktop/main.js");
const historyGroupsSource = read("../lib/history-groups.ts");

for (const label of ["Files", "Side chat", "Browser", "Terminal"]) {
  assert.match(launcher, new RegExp(`text\\(\\"${label}\\"`));
}
assert.doesNotMatch(launcher, /Claude Code|openBuiltinTab\("claude"\)/);
assert.doesNotMatch(launcher, /readBookmarks|readShortcuts|ntpUrlInput/);
assert.match(browserHome, /BrowserImportDialog/);
assert.match(browserHome, /importBookmarkTree/);
assert.match(browserHome, /markBrowserImportPromptFinished/);
assert.match(browserHome, /consumeBrowserImportRequest/);
assert.match(browserHome, /canImport && showImport/);
assert.doesNotMatch(browserHome, /browser-import-dismissed/);
assert.doesNotMatch(browserHome, /readBookmarks|removeBookmark|subscribeBookmarks|ntpBookmarks|ntpBookmark/);
assert.match(browserHome, /readShortcuts/);
assert.match(browserGlyph, /BrowserGlyph/);
assert.match(browserHome, /<BrowserGlyph/);
assert.match(browserPrefs, /SHOW_BOOKMARKS_BAR_KEY/);
assert.match(browserPrefs, /BROWSER_IMPORT_PROMPT_FINISHED_KEY/);
assert.match(browserControls, /function BrowserMenu/);
assert.match(browserControls, /function BookmarkBar/);
assert.doesNotMatch(webTabPane, /SplitButton|Open split view|Columns2/);
assert.match(browserControls, /Show bookmarks bar/);
assert.match(browserControls, /Clear browsing data/);
assert.match(browserControls, /Browser settings/);
assert.match(browserControls, /openBuiltinTab\("bookmarks"\)/);
assert.match(browserControls, /openBuiltinTab\("history"\)/);
assert.match(browserControls, /requestBrowserImport/);
assert.match(browserControls, /disabled: !canImport/);
assert.match(browserControls, /separatorBefore/);
assert.match(browserControls, /checked/);
assert.match(browserSettings, /BrowserImportDialog/);
assert.match(browserSettings, /browserData\.clear/);
assert.match(settingsLayout, /\/settings\/browser/);
assert.match(browserSettingsRoute, /<BrowserSettings/);
assert.match(contextMenu, /item\.checked/);
assert.match(contextMenu, /item\.separatorBefore/);
assert.match(centerTabsCss, /container-type:\s*inline-size/);
assert.match(centerTabsCss, /@container\s*\(max-width:\s*719px\)/);
assert.match(centerTabsCss, /@container\s*\(max-width:\s*559px\)/);
assert.match(centerTabsCss, /@container\s*\(max-width:\s*519px\)/);
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
assert.match(bridge, /browserImport\?: DesktopBrowserImportApi/);
assert.match(bridge, /browserData\?: DesktopBrowserDataApi/);
assert.match(bridge, /stop\(id: string\): void/);
assert.match(preload, /browser-import:list-sources/);
assert.match(preload, /browser-data:clear/);
assert.match(preload, /webtab:stop/);
assert.match(main, /browser-import:run/);
assert.match(main, /browser-data:clear/);
assert.match(main, /webtab:stop/);
assert.match(main, /cookies:\s*result\.cookies/);
assert.doesNotMatch(main, /cookies:\s*result\.(?:cookies\.)?(?:name|value)/);

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
