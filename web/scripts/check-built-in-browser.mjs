import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const launcher = read("../components/center-tabs/new-tab-page.tsx");
const browserHome = read("../components/center-tabs/browser-home-page.tsx");
const mainMenu = read("../components/center-tabs/main-menu.tsx");
const dropdownMenu = read("../components/ui/dropdown-menu.tsx");
const builtin = read("../components/center-tabs/builtin-tab-pane.tsx");
const bridge = read("../lib/desktop-bridge.ts");
const preload = read("../../desktop/preload.js");
const main = read("../../desktop/main.js");
const historyGroupsSource = read("../lib/history-groups.ts");

for (const label of ["Files", "Side chat", "Browser", "Terminal"]) {
  assert.match(launcher, new RegExp(`text\\(\\"${label}\\"`));
}
assert.doesNotMatch(launcher, /readBookmarks|readShortcuts|ntpUrlInput/);
assert.match(browserHome, /BrowserImportDialog/);
assert.match(browserHome, /importBookmarkTree/);
assert.doesNotMatch(browserHome, /readBookmarks|removeBookmark|subscribeBookmarks|ntpBookmarks|ntpBookmark/);
assert.match(browserHome, /readShortcuts/);
assert.match(mainMenu, /getBoundingClientRect\(\)/);
assert.match(mainMenu, /window\.innerWidth\s*-\s*trigger\.right/);
assert.match(mainMenu, /top:\s*Math\.round\(trigger\.bottom\s*\+\s*4\)/);
assert.doesNotMatch(mainMenu, /rightInset:\s*8/);
assert.match(dropdownMenu, /align\s*=\s*"end"/);
assert.match(dropdownMenu, /sideOffset\s*=\s*4/);
assert.match(builtin, /groupHistoryByLocalDate/);
assert.match(builtin, /browsing-history-row/);
assert.match(bridge, /browserImport\?: DesktopBrowserImportApi/);
assert.match(preload, /browser-import:list-sources/);
assert.match(main, /browser-import:run/);
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

execFileSync(process.execPath, [fileURLToPath(new URL("../../desktop/browser-profile-import.js", import.meta.url))], {
  stdio: "pipe",
});

console.log("built-in browser source check passed");
