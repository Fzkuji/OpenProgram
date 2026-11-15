/**
 * Queue persistence: rows survive a reload or restart through localStorage,
 * and a starting window takes only the rows of windows whose lock is free.
 * Web Locks and both storages are in-memory doubles.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

const store = new Map();
globalThis.localStorage = {
  getItem: key => store.get(key) ?? null,
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: key => store.delete(key),
};
const tab = new Map();
globalThis.sessionStorage = {
  getItem: key => tab.get(key) ?? null,
  setItem: (key, value) => tab.set(key, String(value)),
  removeItem: key => tab.delete(key),
};
const held = new Set(["openprogram.send-queue.window:live"]);
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { locks: {
  async request(name, callback) {
    held.add(name);
    const result = callback({ name });
    if (name.startsWith("openprogram.send-queue.window:")) return result;
    try { return await result; } finally { held.delete(name); }
  },
  async query() { return { held: [...held].map(name => ({ name })) }; },
} } });

const { saveQueues, parseQueues, adoptOrphanedQueues } = await import("../../../lib/chat/send/queue-storage.ts");
const KEY = "openprogram.send-queue.v2";
const row = (id, text, extra = {}) => ({ id, text, queuedAt: 1, thinking: "medium", webSearchEnabled: false, background: false, ...extra });
const command = { type: "execution.command", action: "execution.steer", command_id: "c", execution_id: "e",
  expected_version: 7, payload: { message: "steer" } };

test("a window saves its rows under its own entry, never attachment bytes", async () => {
  await adoptOrphanedQueues();
  saveQueues({ A: [row("q1", "plain", { injecting: true }), row("q2", "file", { docs: [{ dataB64: "secret" }] })] });
  const saved = JSON.parse(store.get(KEY));
  const [own] = Object.values(saved);
  assert.deepEqual(own.A.map(r => r.id), ["q1", "q2"]);
  assert.equal(own.A[0].attachmentsStored, undefined);
  assert.equal(own.A[1].attachmentsStored, true);
  assert.equal(JSON.stringify(saved).includes("secret"), false);
  // Read back, a row with files waits for them instead of sending bare.
  const parsed = parseQueues(own);
  assert.equal(parsed.A[1].attachmentsPending, true);
  assert.equal("attachmentsStored" in parsed.A[1], false);
  assert.equal(parsed.A[0].attachmentsPending, undefined);
  saveQueues({});
  assert.equal(store.has(KEY), false);
});

test("a starting window adopts the rows of gone windows only, once", async () => {
  store.set(KEY, JSON.stringify({
    gone: { A: [row("g1", "typed before the restart")], B: [row("g2", "steer", { steerCommand: command, injecting: true })] },
    live: { A: [row("l1", "another open window")] },
  }));
  tab.set("openprogram.pending-steers.v1", JSON.stringify({ C: [row("t1", "held", { steerError: "cancelled" })] }));
  const adopted = await adoptOrphanedQueues();
  assert.deepEqual(adopted.A.map(r => r.text), ["typed before the restart"]);
  assert.equal(adopted.B[0].steerError, "unconfirmed");
  assert.equal(adopted.B[0].injecting, false);
  assert.equal(adopted.C[0].steerError, "cancelled");
  const saved = JSON.parse(store.get(KEY));
  assert.equal(saved.gone, undefined);
  assert.deepEqual(saved.live.A.map(r => r.id), ["l1"]);
  assert.equal(tab.has("openprogram.pending-steers.v1"), false);
  assert.deepEqual(await adoptOrphanedQueues(), {});
});

test("invalid rows and blocked storage are ignored", async () => {
  assert.deepEqual(parseQueues("not rows"), {});
  assert.deepEqual(parseQueues({ A: [row("x", "t", { steerCommand: { ...command, action: "execution.cancel" } })] }), {});
  const previous = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw Error("blocked"); }, setItem() { throw Error("blocked"); }, removeItem() { throw Error("blocked"); } };
  try {
    assert.doesNotThrow(() => saveQueues({ A: [row("q", "t")] }));
    assert.deepEqual(await adoptOrphanedQueues(), {});
  } finally { globalThis.localStorage = previous; }
});
