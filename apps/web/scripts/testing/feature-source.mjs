import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
const webRoot = new URL("../../", import.meta.url);
function read(path) { return readFileSync(new URL(path, webRoot), "utf8"); }
export function readDesktopBridgeSource() {
  const names = readdirSync(new URL("lib/desktop/", webRoot))
    .filter((name) => /^bridge-.*\.ts$/.test(name)).sort();
  return [read("lib/desktop/desktop-bridge.ts"), ...names.map((name) => read(`lib/desktop/${name}`))].join("\n");
}
export function readFileTreeSource() {
  return ["file-tree.tsx", "file-tree-actions.ts", "file-tree-operation.ts", "file-tree-query.ts"]
    .map((name) => read(`components/files/${name}`)).join("\n");
}
export function readDesktopMainSource() {
  return createRequire(import.meta.url)("../../../desktop/scripts/main-source.js").readMainSource();
}
