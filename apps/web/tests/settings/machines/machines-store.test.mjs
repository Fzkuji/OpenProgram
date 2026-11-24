// The machine list in the store: a read that gets no answer keeps the list
// shown and is tried again quietly with a growing wait; an answer the worker
// refuses is not asked again.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";

installLoader({
  stubs: {
    "@/lib/net/ws/ws-request": "export const wsRequest = (...args) => globalThis.__wsRequest(...args);",
    "@/lib/computer-use/computer-use-call": "export const rememberMachineNames = () => {};",
  },
});

const store = await import("../../../lib/machines/store.ts");

const realSetTimeout = globalThis.setTimeout;
const realWarn = console.warn;
let waits = [];
let pending = [];
function fakeTimers() {
  globalThis.setTimeout = (fn, ms) => { waits.push(ms); pending.push(fn); return pending.length; };
}
const runPending = async () => {
  const fns = pending; pending = [];
  for (const fn of fns) fn();
  await new Promise((resolve) => realSetTimeout(resolve, 0));
};

afterEach(() => {
  globalThis.setTimeout = realSetTimeout;
  console.warn = realWarn;
  store.resetMachinesForTests();
  waits = []; pending = [];
});

const SNAPSHOT = { items: [{ id: "box1", name: "Box", enabled: true }], kinds: [] };

test("no answer keeps the list and retries 1 s, 2 s, 4 s until one arrives", async () => {
  console.warn = () => {};
  fakeTimers();
  const answers = [null, null, null, SNAPSHOT];
  let calls = 0;
  globalThis.__wsRequest = async () => answers[calls++];
  const before = store.useMachines.getState().items;
  await store.ensureMachines(true);
  assert.equal(store.useMachines.getState().items, before, "the list shown stays");
  await runPending();
  await runPending();
  await runPending();
  assert.equal(calls, 4);
  assert.deepEqual(waits, [1000, 2000, 4000]);
  assert.deepEqual(store.useMachines.getState().items.map((m) => m.id), ["box1"]);
  assert.equal(pending.length, 0, "stops once the list arrived");
});

test("an answer the worker refuses is not asked again", async () => {
  console.warn = () => {};
  fakeTimers();
  let calls = 0;
  globalThis.__wsRequest = async () => { calls += 1; return { status: "error", error_code: "invalid_request" }; };
  await store.ensureMachines(true);
  assert.equal(calls, 1);
  assert.equal(pending.length, 0);
});

test("a broadcast that lands while a retry waits cancels it", async () => {
  console.warn = () => {};
  globalThis.__wsRequest = async () => null;
  await store.ensureMachines(true);
  store.onMachinesMessage(SNAPSHOT);
  let calls = 0;
  globalThis.__wsRequest = async () => { calls += 1; return null; };
  await new Promise((resolve) => realSetTimeout(resolve, 1100));
  assert.equal(calls, 0);
});
