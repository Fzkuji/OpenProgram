/**
 * The shared clock: one timer for every subscriber, set for the moment
 * the earliest text changes, gone when nobody watches, paused while the
 * document is hidden. Also the optimistic-action helper, which settles on
 * a store change instead of polling.
 */
import assert from "node:assert/strict";
import { mock, test } from "node:test";

mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });

const doc = new EventTarget();
doc.visibilityState = "visible";
globalThis.document = doc;

const { watchNow, SECONDS, AGO, COUNTDOWN, whenChanges, clockTimersForTest } = await import(
  "../../lib/clock/use-now.ts"
);
const { optimisticAction } = await import("../../lib/runtime-bridge/optimistic-action.ts");

test("cadences name the next change of their text", () => {
  assert.equal(SECONDS(0), 1000);
  assert.equal(SECONDS(1500), 2000);
  assert.equal(AGO(30_000), 60_000);
  assert.equal(AGO(2 * 3_600_000 + 5), 3 * 3_600_000);
  assert.equal(COUNTDOWN(-2500), -2000);
  assert.equal(COUNTDOWN(0), Infinity);
  const minutes = whenChanges((elapsed) => Math.floor(elapsed / 60_000));
  assert.equal(minutes(10_000), 60_000);
  assert.equal(whenChanges(() => "same")(0), Infinity);
});

test("one timer serves every subscriber, each woken only when its text changes", () => {
  const start = Date.now();
  const fast = [];
  const slow = [];
  const stopFast = watchNow(SECONDS, start, (now) => fast.push(now - start));
  const stopSlow = watchNow(AGO, start, (now) => slow.push(now - start));
  assert.deepEqual(clockTimersForTest(), { slots: 2, armed: true });

  mock.timers.tick(999);
  assert.deepEqual(fast, []);
  mock.timers.tick(1);
  assert.deepEqual(fast, [1000]);
  for (let i = 0; i < 59; i++) mock.timers.tick(1000);
  assert.equal(fast.length, 60);
  assert.deepEqual(slow, [60_000]);

  stopFast();
  assert.deepEqual(clockTimersForTest(), { slots: 1, armed: true });
  stopSlow();
  assert.deepEqual(clockTimersForTest(), { slots: 0, armed: false }, "no subscriber, no timer");
});

test("a hidden document stops the clock; shown again, it catches up at once", () => {
  const start = Date.now();
  const seen = [];
  const stop = watchNow(SECONDS, start, (now) => seen.push(now - start));
  doc.visibilityState = "hidden";
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(clockTimersForTest().armed, false);
  mock.timers.tick(5000);
  assert.deepEqual(seen, []);
  doc.visibilityState = "visible";
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.deepEqual(seen, [5000]);
  assert.equal(clockTimersForTest().armed, true);
  stop();
});

test("a countdown rests at zero without a timer", () => {
  const due = Date.now() + 1500;
  const seen = [];
  const stop = watchNow(COUNTDOWN, due, (now) => seen.push(due - now));
  mock.timers.tick(500);
  mock.timers.tick(1000);
  assert.deepEqual(seen, [1000, 0]);
  assert.equal(clockTimersForTest().armed, false);
  stop();
});

test("an optimistic action settles on the store's change, with one deadline", () => {
  const listeners = new Set();
  const subscribe = (fn) => (listeners.add(fn), () => listeners.delete(fn));
  let landed = false;
  let reverted = false;
  optimisticAction({ apply() {}, settled: () => landed, subscribe, revert: () => { reverted = true; } });
  assert.equal(listeners.size, 1);
  for (const fn of listeners) fn();
  assert.equal(listeners.size, 1, "an unrelated change keeps it watching");
  landed = true;
  for (const fn of [...listeners]) fn();
  assert.equal(listeners.size, 0, "settled: it stops listening");
  mock.timers.tick(10_000);
  assert.equal(reverted, false);

  landed = false;
  optimisticAction({ apply() {}, settled: () => landed, subscribe, revert: () => { reverted = true; } });
  mock.timers.tick(10_000);
  assert.equal(reverted, true, "unsettled at the deadline: reverted");
  assert.equal(listeners.size, 0);
});
