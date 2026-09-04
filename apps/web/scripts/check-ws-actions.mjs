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

/* ---- frontend side: action literals in actual WS envelopes ------------ */

// Actions assembled at runtime rather than written as a literal. Each
// entry must name the reason it can't be checked statically.
const DYNAMIC = new Set([
  // none today — add here with a justification, don't widen the regex
]);

/** Find the end of an object literal without treating braces in strings or
 * comments as structure. This is intentionally a small lexical scanner:
 * the check only needs to inspect the top-level fields of send envelopes,
 * not parse TypeScript. */
function objectEnd(text, open) {
  let depth = 0;
  let quote = null;
  let lineComment = false;
  let blockComment = false;
  for (let i = open; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (lineComment) {
      if (ch === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (ch === "*" && next === "/") {
        blockComment = false;
        i += 1;
      }
      continue;
    }
    if (quote) {
      if (ch === "\\") i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "/" && next === "/") {
      lineComment = true;
      i += 1;
      continue;
    }
    if (ch === "/" && next === "*") {
      blockComment = true;
      i += 1;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "{") depth += 1;
    if (ch === "}" && --depth === 0) return i;
  }
  return -1;
}

function topLevelActions(text, open, close) {
  const actions = [];
  let depth = 0;
  let quote = null;
  let lineComment = false;
  let blockComment = false;
  for (let i = open + 1; i < close; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (lineComment) {
      if (ch === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (ch === "*" && next === "/") {
        blockComment = false;
        i += 1;
      }
      continue;
    }
    if (quote) {
      if (ch === "\\") i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "/" && next === "/") {
      lineComment = true;
      i += 1;
      continue;
    }
    if (ch === "/" && next === "*") {
      blockComment = true;
      i += 1;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "{") {
      depth += 1;
      continue;
    }
    if (ch === "}") {
      depth -= 1;
      continue;
    }
    if (depth === 0) {
      const match = text.slice(i).match(/^action\s*:\s*(["'])([\w.:-]+)\1/);
      if (match) {
        actions.push({ action: match[2], offset: i });
        i += match[0].length - 1;
      }
    }
  }
  return actions;
}

/** Return literal action fields from actual WS send calls. Plain object
 * fields elsewhere are intentionally ignored: they include local callback
 * types, REST bodies, revision commands, and other non-WS vocabularies. */
function sentActions(text) {
  const actions = [];
  const sender =
    /\b(?:wsSend|send|[A-Za-z_$][\w$]*\.send)\s*\(\s*(?:JSON\.stringify\s*\(\s*)?\{/g;
  for (const call of text.matchAll(sender)) {
    const open = call.index + call[0].lastIndexOf("{");
    const close = objectEnd(text, open);
    if (close < 0) continue;
    actions.push(...topLevelActions(text, open, close));
  }
  return actions;
}

const hits = [];
for (const path of sources(webRoot)) {
  // This file lists action names on purpose.
  if (path.endsWith("check-ws-actions.mjs")) continue;
  const text = readFileSync(path, "utf8");
  for (const { action: act, offset } of sentActions(text)) {
    if (registered.has(act) || DYNAMIC.has(act)) continue;
    const lineNo = text.slice(0, offset).split("\n").length;
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
