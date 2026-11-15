import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const web = fileURLToPath(new URL("../../../", import.meta.url));

function sources(dir) {
  return readdirSync(join(web, dir)).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(join(web, path)).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

// The transcript applies only the reply to a session read it registered
// (runtime-bridge/session/session-load.ts). A reload sent straight down the socket
// after a branch switch is dropped once the session has been read, and the
// view stays on the branch it left.
test("every reload after a branch switch goes through the managed session read", () => {
  const raw = [];
  for (const path of [...sources("components"), ...sources("lib")]) {
    const source = readFileSync(join(web, path), "utf8");
    if (!/action: ["']load_session["']/.test(source)) continue;
    const helper = /function wsSend\(payload: unknown\)[^{]*\{([\s\S]*?)\n\}/.exec(source);
    if (helper && /\.send\(JSON\.stringify/.test(helper[1]) && /wsSend\(\{ action: ["']load_session["']/.test(source)) raw.push(path);
  }
  assert.deepEqual(raw, []);
});
