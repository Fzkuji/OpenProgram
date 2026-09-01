/** Every `action: "…"` the frontend sends must name a real backend handler.
 *
 *  `_handle_ws_command` looks the action up in `WS_ACTIONS` — the union
 *  of the `ACTIONS` dicts in
 *  `apps/server/openprogram_server/_webui/ws_actions/*.py`. An
 *  action with no entry there now gets an `operation_error` frame back, but
 *  for a long time it was dropped in total silence: no handler, no error,
 *  no log. `/branch` shipped that way, sending `create_branch` to a
 *  backend that never had such a handler, and the command simply did
 *  nothing forever. A name that exists on one side only is invisible at
 *  runtime, so it is guarded here instead.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// fileURLToPath, not .pathname — a repo path containing a space arrives
// percent-encoded otherwise and every readdir misses.
const webRoot = fileURLToPath(new URL("../", import.meta.url));
const wsActionsDir = fileURLToPath(
  new URL("../../server/openprogram_server/_webui/ws_actions/", import.meta.url),
);
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

/* ---- backend side: keys of every `ACTIONS = { … }` dict ------------- */

const registered = new Set();
for (const name of readdirSync(wsActionsDir)) {
  if (!name.endsWith(".py")) continue;
  const text = readFileSync(join(wsActionsDir, name), "utf8");
  const block = text.match(/^ACTIONS\s*=\s*\{([\s\S]*?)^\}/m);
  if (!block) continue;
  for (const m of block[1].matchAll(/["']([\w.:-]+)["']\s*:/g)) {
    registered.add(m[1]);
  }
}

assert.ok(
  registered.size > 20,
  `only ${registered.size} backend actions parsed — the ACTIONS dict `
    + "shape changed and this check is no longer reading it",
);

/* ---- frontend side: every `action: "…"` literal --------------------- */

// Actions assembled at runtime rather than written as a literal. Each
// entry must name the reason it can't be checked statically.
const DYNAMIC = new Set([
  // none today — add here with a justification, don't widen the regex
]);

/** True when this `action:` belongs to an HTTP request body rather than a
 *  WS frame — REST routes have their own `action` vocabulary
 *  (`/api/skills/discovery/sources` takes `{action: "add"}`) which has
 *  nothing to do with the socket registry. Detected by a `body:` on the
 *  same line or the two above it — that is how every fetch call here is
 *  written. Deliberately NOT keyed on `JSON.stringify`, which also wraps
 *  genuine `socket.send(JSON.stringify({action: …}))` frames. */
function isHttpBody(lines, i) {
  return lines
    .slice(Math.max(0, i - 2), i + 1)
    .some((l) => /\bbody:/.test(l));
}

/** TypeScript method parameter types can also contain `action: "…"`.
 * They describe a local bridge argument, not a WebSocket frame field. */
function isMethodParameterType(line) {
  return /^\s*\w+\??\([^)]*\baction:\s*["'][^;]+\)\s*:/.test(line);
}

/** `wsRequest("action", …)` / `filesWsRequest<T>("action", …)` pass the
 *  action as a positional first argument, usually on its own line — the
 *  per-line `action:` regex above never sees those. The generic parameter
 *  (`<TreeResult>`, `<{ … }>`) never contains a paren, so `[^(]*?` skips
 *  it safely; the opener and the string may sit on different lines, so
 *  this runs over the whole file text. Template-literal actions
 *  (`` `project_file_${op}` ``) stay invisible — list them in DYNAMIC. */
const WS_REQUEST_RE =
  /\b(?:files)?[wW]sRequest(?:<[^(]*?>)?\(\s*["']([\w.:-]+)["']/g;

const hits = [];
for (const path of sources(webRoot)) {
  // This file lists action names on purpose.
  if (path.endsWith("check-ws-actions.mjs")) continue;
  const text = readFileSync(path, "utf8");
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    if (/^\s*(\*|\/\/)/.test(line)) return;
    if (isMethodParameterType(line)) return;
    for (const m of line.matchAll(/\baction:\s*["']([\w.:-]+)["']/g)) {
      const act = m[1];
      if (registered.has(act) || DYNAMIC.has(act)) continue;
      if (isHttpBody(lines, i)) continue;
      hits.push(`${path.slice(webRoot.length)}:${i + 1}: action "${act}"`);
    }
  });
  for (const m of text.matchAll(WS_REQUEST_RE)) {
    const act = m[1];
    if (registered.has(act) || DYNAMIC.has(act)) continue;
    const lineNo = text.slice(0, m.index).split("\n").length;
    hits.push(`${path.slice(webRoot.length)}:${lineNo}: action "${act}"`);
  }
}

assert.deepEqual(
  hits,
  [],
  "these actions have no handler in "
    + "apps/server/openprogram_server/_webui/ws_actions/*.py "
    + `ACTIONS — the backend will answer with operation_error:\n${hits.join("\n")}`,
);

console.log(`check-ws-actions: ok (${registered.size} backend actions)`);
