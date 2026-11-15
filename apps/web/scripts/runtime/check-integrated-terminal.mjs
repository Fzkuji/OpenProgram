// Rule: every --terminal-* colour the terminal page reads is defined by the
// global theme styles, the xterm stylesheet is loaded by the root layout,
// and the preload's terminal resize goes over the terminal:resize channel.
// The page's behaviour is tested in tests/desktop/terminal-page.test.mjs.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

const root = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");
const cssIn = (dir) => readdirSync(new URL(dir, root)).filter((name) => name.endsWith(".css")).map((name) => read(`${dir}${name}`));

const page = read("components/center-tabs/pages/terminal-page.tsx");
const styles = [...cssIn("app/styles/design/"), ...cssIn("app/styles/themes/"), ...cssIn("app/styles/base/")].join("\n");
const tokens = [...new Set([...page.matchAll(/"(--terminal-[a-z-]+)"/g)].map((match) => match[1]))];
assert.ok(tokens.length >= 20, "the terminal theme reads its colours from --terminal-* tokens");
for (const token of tokens) {
  assert.match(styles, new RegExp(`${token}\\s*:`), `${token} is defined in the global styles`);
}

assert.match(read("app/layout.tsx"), /@xterm\/xterm\/css\/xterm\.css/, "the root layout loads xterm's stylesheet");
assert.match(read("../desktop/main/preload.js"), /resize: \(id, cols, rows\) => ipcRenderer\.send\("terminal:resize", id, cols, rows\)/);
