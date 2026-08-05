/** The WebSocket lives in `runtimeState.ws`, reached via `getSocket()`.
 *
 *  Nothing ever assigns `window.ws`, so any component reading it got a
 *  permanently-undefined socket and silently skipped its send. Eight
 *  files did — which is why retry / branch / rewind / version-switch
 *  never refreshed the transcript (their `load_session` was dropped on
 *  the floor) and the fix only appeared after a manual page reload.
 *  A dead read is invisible at runtime, so it is guarded here instead.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// fileURLToPath, not .pathname — a repo path containing a space arrives
// percent-encoded otherwise and every readdir misses.
const root = fileURLToPath(new URL("../", import.meta.url));
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "dist"]);

/** Every .ts/.tsx under web/, minus build output. */
function* sources(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      yield* sources(path);
    } else if (/\.tsx?$/.test(name)) {
      yield path;
    }
  }
}

// `window.ws`, `w.ws` after a `window` cast, and the cast itself.
const OFFENDERS = [
  /\bwindow\s*\.\s*ws\b/,
  /\bw\s*\.\s*ws\b/,
  /\{\s*ws\?:\s*WebSocket/,
];

const hits = [];
for (const path of sources(root)) {
  const text = readFileSync(path, "utf8");
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    // A comment saying the socket is NOT on window is the point of this
    // check, not a violation of it.
    if (/^\s*(\*|\/\/)/.test(line)) return;
    if (OFFENDERS.some((re) => re.test(line))) {
      hits.push(`${path.slice(root.length)}:${i + 1}: ${line.trim()}`);
    }
  });
}

assert.deepEqual(
  hits,
  [],
  "the socket is not on `window` — use `getSocket()` from "
    + `lib/runtime-bridge/state:\n${hits.join("\n")}`,
);

console.log("check-socket-access: ok");
