/** Every IPC channel the renderer side uses must exist on the main side.
 *
 *  The desktop bridge (lib/desktop/desktop-bridge*.ts and its types) only
 *  reaches Electron through the preloads, which call
 *  `ipcRenderer.invoke/send/sendSync("channel")`. A channel with no
 *  `ipcMain.handle/on` (or the web tab's `webContents.ipc.on`) in the main process is invisible at runtime: an
 *  `invoke` rejects with "No handler registered" and a `send` is dropped in
 *  silence. Likewise a preload that listens (`ipcRenderer.on`, or the
 *  preload's `fanOut`) to a channel the main process never sends waits
 *  forever. Both names live in different processes, so they are guarded
 *  here, the way check-ws-actions guards WebSocket actions.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = fileURLToPath(new URL("../../../desktop/", import.meta.url));

function* jsFiles(dir) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* jsFiles(path);
    else if (/\.c?js$/.test(name)) yield path;
  }
}

const QUOTED = String.raw`\s*["'\x60]([\w:.-]+)["'\x60]`;
const collect = (text, pattern) => [...text.matchAll(pattern)].map((match) => match[1]);
/** Channels named in an array literal right after a `for (const [channel, …] of [` (a handler loop). */
const loopChannels = (text) => [...text.matchAll(/for \(const \[channel\b[^\]]*\] of \[([\s\S]*?)\]\)\s*\{/g)]
  .flatMap((match) => collect(match[1], /\[\s*["']([\w:.-]+)["']/g));

const preloads = ["main/preload.js", "main/web-views/webtab-preload.js"].map((path) => readFileSync(join(desktopRoot, path), "utf8"));
const mainFiles = [join(desktopRoot, "main.js"), ...jsFiles(join(desktopRoot, "main"))]
  .filter((path) => !/preload\.js$/.test(path));
const main = mainFiles.map((path) => readFileSync(path, "utf8")).join("\n");

/* ---- main side ------------------------------------------------------- */
const handled = new Set([
  ...collect(main, new RegExp(`ipcMain\\.(?:handle|handleOnce|on|once)\\(${QUOTED}`, "g")),
  // A web tab's own preload talks to its webContents' scoped `ipc`.
  ...collect(main, new RegExp(`\\.ipc\\??\\.(?:handle|handleOnce|on|once)\\(${QUOTED}`, "g")),
  ...loopChannels(main),
]);
// `webContents.send("x"`, `sender.send?.("x"`, and the app menu's local
// `send("x")` helper that forwards to the focused window.
const sentByMain = new Set(collect(main, new RegExp(`\\bsend(?:\\?\\.)?\\(${QUOTED}`, "g")));

/* ---- renderer side --------------------------------------------------- */
const called = new Map();
const listened = new Map();
for (const [index, text] of preloads.entries()) {
  const file = index === 0 ? "main/preload.js" : "main/web-views/webtab-preload.js";
  for (const name of collect(text, new RegExp(`ipcRenderer\\.(?:invoke|send|sendSync)\\(${QUOTED}`, "g"))) called.set(name, file);
  for (const name of collect(text, new RegExp(`(?:ipcRenderer\\.(?:on|once)|fanOut)\\(${QUOTED}`, "g"))) listened.set(name, file);
}
assert.ok(called.size > 50, `the preloads call IPC channels (found ${called.size}); the pattern has gone stale`);
assert.ok(handled.size > 50, `the main process registers IPC handlers (found ${handled.size}); the pattern has gone stale`);

const unhandled = [...called].filter(([name]) => !handled.has(name)).map(([name, file]) => `${file}: ${name}`);
assert.deepEqual(unhandled, [], `preload channels with no ipcMain handler:\n  ${unhandled.join("\n  ")}`);

// Known dead listeners, each with why it is tolerated. An entry must still be
// dead: once the main process sends it again, or the preload drops it, the
// entry goes.
const DEAD_LISTENERS = {
  // Main stopped sending it; the web side's subscribeBrowserHumanInput is a
  // no-op kept for older bridge consumers. The preload's onHumanInput can go.
  "webtab:human-input": "main/preload.js",
};
for (const [name, file] of Object.entries(DEAD_LISTENERS)) {
  assert.equal(listened.get(name), file, `${name} is no longer listened to; drop it from DEAD_LISTENERS`);
  assert.equal(sentByMain.has(name), false, `${name} is sent again; drop it from DEAD_LISTENERS`);
}
const unsent = [...listened]
  .filter(([name]) => !sentByMain.has(name) && !Object.hasOwn(DEAD_LISTENERS, name))
  .map(([name, file]) => `${file}: ${name}`);
assert.deepEqual(unsent, [], `preload listens to channels the main process never sends:\n  ${unsent.join("\n  ")}`);
