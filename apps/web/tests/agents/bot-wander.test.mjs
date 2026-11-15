import assert from "node:assert/strict";
import test from "node:test";

const { AHEAD_SHARE, HOLD_MS, REACH, nextLook, startWander } = await import("../../components/avatar/bot/wander.ts");

/** A seeded random source and a clock whose timers run when told. */
function fakeClock(seed) {
  let state = seed >>> 0;
  const timers = new Map();
  let id = 0;
  return {
    random() { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; },
    setTimeout(run, ms) { timers.set(++id, { run, ms }); return id; },
    clearTimeout(handle) { timers.delete(handle); },
    timers,
    fire() { const [[key, timer]] = timers; timers.delete(key); timer.run(); return timer.ms; },
  };
}

test("glances go well off to the side, and now and then back to ahead", () => {
  const clock = fakeClock(7);
  const looks = Array.from({ length: 2000 }, () => nextLook(clock.random));
  const ahead = looks.filter((look) => look === null).length / looks.length;
  assert.ok(Math.abs(ahead - AHEAD_SHARE) < 0.05, String(ahead));
  const off = looks.filter(Boolean);
  for (const look of off) {
    assert.ok(Math.abs(look.x) >= REACH.x * 0.35 - 1e-9 && Math.abs(look.x) <= REACH.x + 1e-9);
    assert.ok(Math.abs(look.y) <= REACH.y + 1e-9);
  }
  assert.ok(off.some((look) => look.x < 0) && off.some((look) => look.x > 0));
  assert.ok(off.some((look) => look.y < -0.3) && off.some((look) => look.y > 0.3));
});

test("each character keeps glancing, holding each look for a while, until stopped", () => {
  const clock = fakeClock(3);
  const seen = [];
  const stop = startWander((look) => seen.push(look), clock);
  assert.equal(seen.length, 0);
  const first = clock.fire();
  assert.ok(first >= 0 && first <= HOLD_MS.max);
  for (let k = 0; k < 20; k++) {
    const held = clock.fire();
    assert.ok(held >= HOLD_MS.min && held <= HOLD_MS.max, String(held));
  }
  assert.equal(seen.length, 21);
  assert.ok(new Set(seen.map((look) => JSON.stringify(look))).size > 10);
  stop();
  assert.equal(clock.timers.size, 0);
});

test("characters started together do not look the same way at the same time", () => {
  const clocks = [fakeClock(11), fakeClock(12), fakeClock(13)];
  const pauses = clocks.map((clock) => { startWander(() => {}, clock); return [...clock.timers.values()][0].ms; });
  assert.equal(new Set(pauses).size, pauses.length);
});
