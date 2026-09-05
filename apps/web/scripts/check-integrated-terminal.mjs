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
const bridgeTypes = read("lib/desktop-bridge-types.ts");
const preload = read("../desktop/preload.js");
const layout = read("app/layout.tsx");
const css = read("components/center-tabs/center-tabs.module.css");
const base = read("app/styles/base.css");
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
assert.match(page, /PROJECT_RESOLVE_GRACE_MS/);
assert.match(page, /setStartCwd\(\(current\) => current === undefined \? null : current\)/);
assert.match(page, /cwd: startCwd \?\? undefined/);
assert.match(page, /readTerminalTheme/);
for (const color of [
  "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
  "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue",
  "brightMagenta", "brightCyan", "brightWhite",
]) assert.match(page, new RegExp(`\\b${color}:`));
assert.match(page, /attachCustomKeyEventHandler/);
assert.match(page, /api\.write\(id, "\\x03"\)/);
assert.match(page, /navigator\.clipboard\.writeText/);
assert.match(page, /navigator\.clipboard\.readText/);
assert.match(page, /terminal\.clear\(\)/);
assert.match(page, /\\x1b\[2J\\x1b\[3J\\x1b\[H/);
assert.match(page, /restartTerminal/);
assert.match(page, /stopTerminal/);
assert.match(page, /api\.stop\(id\)/);
assert.match(page, /status === "running"/);
assert.match(page, /payload\.done/);
assert.match(page, /options\.disableStdin = true/);
assert.doesNotMatch(page, /<input|terminalInputRow/);
assert.doesNotMatch(page, /background:\s*"#[0-9a-f]{3,8}"/i);
assert.match(css, /\.terminalActions/);
assert.match(css, /\.terminalAction/);
assert.match(css, /background:\s*var\(--terminal-bg\)/);
assert.match(base, /--terminal-bg:/);
assert.match(base, /--terminal-bright-white:/);
assert.match(bridge, /export function destroyStaleTerminals/);
assert.match(bridge, /destroyStaleTerminals\(bridge, tabs\)/);
assert.match(bridge, /useCenterTabs\.subscribe\(reconcileNativeResources\)/);
assert.match(bridge, /reconcileNativeResources\(\)/);
assert.match(bridgeTypes, /resize\(id: string, cols: number, rows: number\): void/);
assert.match(preload, /terminal:resize/);
assert.match(layout, /@xterm\/xterm\/css\/xterm\.css/);
assert.equal(typeof pkg.dependencies?.["@xterm/xterm"], "string");
assert.equal(typeof pkg.dependencies?.["@xterm/addon-fit"], "string");

console.log("integrated terminal web checks passed");
