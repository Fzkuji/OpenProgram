// Rules of the transcript model, checked against the source. No scan has an
// allowlist: code that breaks a rule fails the test. The server's rules live in
// tests/unit/webui/test_transcript_contracts.py.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const WEB = fileURLToPath(new URL("../../../", import.meta.url));

function sources(directory) {
  return readdirSync(join(WEB, directory), { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

const STATUS = "(?:running|streaming|pending|cancelling|queued|paused|cancelled|interrupted|completed|errored)";
const STATUS_LIST = new RegExp(`[\\[(]\\s*['"]${STATUS}['"]\\s*,[^\\])]*['"]${STATUS}['"][^\\])]*[\\])]`, "g");
const STATUS_CHAIN = new RegExp(`status\\w*\\s*===\\s*['"]${STATUS}['"]\\s*\\|\\|\\s*[\\w.?!]*[sS]tatus\\w*\\s*===\\s*['"]${STATUS}['"]`, "g");

/** Other vocabularies that share some words with a row's status. */
const NOT_ROW_STATUS = new Set([
  "lib/chat/transcript/row-status.ts", // the table itself
  "components/chat/messages/execution/gui-tool-view.ts", // a GUI run's execution status
  "lib/chat/send/steer-message.ts", // an execution snapshot's status
  "lib/runtime-bridge/session/chat-handlers.ts", // terminalFunctionStatuses reads call-tree roots
]);

test("no transcript row status list lives outside the status table", () => {
  const found = ["components/chat", "lib/chat", "lib/session-store", "lib/runtime-bridge/session"]
    .flatMap(sources)
    .filter((path) => !NOT_ROW_STATUS.has(relative(".", path)))
    .flatMap((path) => {
      const source = readFileSync(join(WEB, path), "utf8");
      return [...source.matchAll(STATUS_LIST), ...source.matchAll(STATUS_CHAIN)].map((match) => `${path}: ${match[0]}`);
    });
  assert.deepEqual(found, []);
});

test("no message component pairs children to blocks by taking the next one", () => {
  const found = sources("components/chat/messages")
    .filter((path) => /\.shift\(\)|Fifo\b/.test(readFileSync(join(WEB, path), "utf8")));
  assert.deepEqual(found, []);
});

const ROW_CODE = ["components", "lib"];
const TYPED_ROW_CODE = ["components/chat", "lib/chat", "lib/session-store", "lib/runtime-bridge/session"];
/** Source text without comments, so a rule reads code only. */
const code = (path) => readFileSync(join(WEB, path), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const matches = (directories, pattern, skip = new Set()) => directories.flatMap(sources)
  .filter((path) => !skip.has(relative(".", path)))
  .flatMap((path) => [...code(path).matchAll(pattern)].map((match) => `${path}: ${match[0]}`));

test("only the store, fed by the reducer, writes rows", () => {
  const writes = /\b(?:rowsByView|messageOrder)\s*:|\b(?:rowsByView|messageOrder)\[[^\]]*\]\s*=[^=]/g;
  assert.deepEqual(matches(ROW_CODE, writes, new Set(["lib/session-store/index.ts"])), []);
  const unfed = ROW_CODE.flatMap(sources).filter((path) => {
    const source = code(path);
    return /\.writeView\(/.test(source) && !/from "@\/lib\/chat\/transcript\/apply-transcript-update"/.test(source);
  });
  assert.deepEqual(unfed, [], "writeView takes only what apply-transcript-update returned");
});

test("no client code builds a reply id from another id", () => {
  assert.deepEqual(matches(ROW_CODE, /_reply[`"']|_user_reply|endsWith\(\s*["'`]_reply/g), []);
});

test("nothing in the web client asks for a whole conversation again", () => {
  assert.deepEqual(matches(ROW_CODE, /session_reload/g), []);
});

test("no row's type is decided from its display, function or content", () => {
  const decisions = /\b(?:msg|row|message|m|r|card|held|reply|child|prev|next|last|first)\??\.(?:display|function)\s*[!=]==?|\b(?:msg|row|message|m|r)\??\.content\??\.(?:startsWith|match|includes)\(/g;
  assert.deepEqual(matches(TYPED_ROW_CODE, decisions), []);
});

test("rows are folded only in the transcript package", () => {
  // Passing a server field on as itself (`attachCards: msg.attachCards`) folds nothing.
  const folds = /\b(attachCards|runtimeChildren|steeringReplyId|continuedBy|continues|coveredBy)\s*:(?!\s*\w+\??\.\1\b)/g;
  const transcript = (path) => path.startsWith("lib/chat/transcript/") || path === "lib/session-store/types.ts";
  const found = ROW_CODE.flatMap(sources).filter((path) => !transcript(relative(".", path)))
    .flatMap((path) => [...code(path).matchAll(folds)].map((match) => `${path}: ${match[0]}`));
  assert.deepEqual(found, []);
});
