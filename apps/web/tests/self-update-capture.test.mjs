import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function entry(bridge, getSocket) {
  const source = readFileSync(new URL("../lib/desktop-bridge.ts", import.meta.url), "utf8");
  const file = ts.createSourceFile("desktop-bridge.ts", source, ts.ScriptTarget.Latest, true);
  let callback;
  function visit(node) {
    if (ts.isCallExpression(node) && node.arguments[0]?.text === "op:ws-message") callback = node.arguments[1];
    ts.forEachChild(node, visit);
  }
  visit(file);
  assert.ok(callback, "actual Desktop WS listener is registered");
  const code = ts.transpileModule(`(${callback.getText(file)})`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return vm.runInNewContext(code, { bridge, getSocket, WebSocket: { OPEN: 1 } });
}
const nonce = "a".repeat(64);
const event = (data = {}) => ({ detail: { type: "webtab.command",
  data: { op: "self_update_capture", req_id: "request", window_id: "main", nonce, ...data } } });
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("main capture forwards only nonce and returns no native payload", async () => {
  const calls = [], sent = [];
  const ws = { readyState: 1, send: (value) => sent.push(JSON.parse(value)) };
  const handler = entry({ windowId: "main", selfUpdateCapture: async (...args) => {
    calls.push(args); return { ok: true, image: "must not relay", credential: "private" };
  } }, () => ws);
  handler(event({ arbitrary_command: "ignored" }));
  await settle();
  assert.deepEqual(calls, [[nonce]]);
  assert.deepEqual(sent, [{ action: "webtab_result", req_id: "request", ok: true, window_id: "main" }]);
});

test("detached, old, invalid or failed capture cannot report success", async () => {
  for (const mode of ["detached", "old", "nonce", "window", "failure"]) {
    const sent = [];
    const ws = { readyState: 1, send: (value) => sent.push(JSON.parse(value)) };
    let calls = 0;
    const bridge = { windowId: mode === "detached" ? "other" : "main", selfUpdateCapture: async () => {
      calls++; throw new Error("unavailable");
    } };
    if (mode === "old") delete bridge.selfUpdateCapture;
    entry(bridge, () => ws)(event(mode === "nonce" ? { nonce: "invalid" } : mode === "window" ? { window_id: "other" } : {}));
    await settle();
    assert.equal(sent[0].ok, false, mode);
    assert.equal(calls, mode === "failure" ? 1 : 0, mode);
  }
});

test("reconnected socket never receives the previous capture reply", async () => {
  const sent = [];
  let finish;
  const ws = { readyState: 1, send: (value) => sent.push(value) };
  let current = ws;
  entry({ windowId: "main", selfUpdateCapture: () => new Promise((resolve) => { finish = resolve; }) }, () => current)(event());
  current = { ...ws };
  finish({ ok: true });
  await settle();
  assert.deepEqual(sent, []);
});
