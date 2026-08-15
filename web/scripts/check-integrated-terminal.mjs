import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const ids = read("lib/state/center-tab-ids.ts");
const launcher = read("components/center-tabs/new-tab-page.tsx");
const shell = read("components/app-shell.tsx");
const page = read("components/center-tabs/terminal-page.tsx");
const bridge = read("lib/desktop-bridge.ts");
const preload = read("../desktop/preload.js");
const layout = read("app/layout.tsx");
const pkg = JSON.parse(read("package.json"));

assert.match(ids, /"terminal"/);
assert.match(ids, /"claude"/);
assert.match(launcher, /openBuiltinTab\("terminal"\)/);
assert.match(shell, /<TerminalPage preset="shell"/);
assert.match(shell, /<TerminalPage preset="claude"/);
assert.match(page, /import\("@xterm\/xterm"\)/);
assert.match(page, /import\("@xterm\/addon-fit"\)/);
assert.match(page, /new ResizeObserver/);
assert.match(page, /api\.resize/);
assert.match(page, /dataset\.processId/);
assert.doesNotMatch(page, /api\.stop/);
assert.doesNotMatch(page, /<input|terminalInputRow/);
assert.match(bridge, /export function destroyStaleTerminals/);
assert.match(bridge, /destroyStaleTerminals\(bridge, tabs\)/);
assert.match(bridge, /useCenterTabs\.subscribe\(reconcileNativeResources\)/);
assert.match(bridge, /reconcileNativeResources\(\)/);
assert.match(bridge, /resize\(id: string, cols: number, rows: number\): void/);
assert.match(preload, /terminal:resize/);
assert.match(layout, /@xterm\/xterm\/css\/xterm\.css/);
assert.equal(typeof pkg.dependencies?.["@xterm/xterm"], "string");
assert.equal(typeof pkg.dependencies?.["@xterm/addon-fit"], "string");

console.log("integrated terminal web checks passed");
