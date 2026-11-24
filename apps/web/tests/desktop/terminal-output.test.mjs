import assert from "node:assert/strict";
import test from "node:test";

const { followTerminalOutput, outputFor } = await import("../../lib/desktop/terminal-output.ts");

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

function scheduler() {
  const due = [];
  return {
    due,
    schedule: (run, ms) => { const item = { run, ms }; due.push(item); return item; },
    cancel: (item) => { const at = due.indexOf(item); if (at >= 0) due.splice(at, 1); },
  };
}

test("output is read when the terminal writes some, never on a timer", async () => {
  const clock = scheduler();
  let reads = 0;
  const follower = followTerminalOutput(async () => { reads++; return "idle"; }, clock);
  follower.poke();
  await flush();
  assert.equal(reads, 1);
  assert.equal(clock.due.length, 0, "an idle terminal leaves nothing scheduled");
  follower.poke();
  await flush();
  assert.equal(reads, 2);
  follower.dispose();
});

test("reads never overlap: a push during a read reads once more after it", async () => {
  const clock = scheduler();
  const answers = [];
  let reads = 0;
  const follower = followTerminalOutput(() => { reads++; return new Promise(resolve => answers.push(resolve)); }, clock);
  follower.poke();
  follower.poke();
  follower.poke();
  assert.equal(reads, 1);
  answers.shift()("idle");
  await flush();
  assert.equal(reads, 2, "the pushes during the read make one more");
  answers.shift()("idle");
  await flush();
  assert.equal(reads, 2);
  follower.dispose();
});

test("more output already waiting is read at once", async () => {
  const clock = scheduler();
  const results = ["more", "more", "idle"];
  let reads = 0;
  const follower = followTerminalOutput(async () => { reads++; return results.shift(); }, clock);
  follower.poke();
  await flush();
  assert.equal(reads, 3);
  follower.dispose();
});

test("a failed read is tried again later each time; another instance stops reading", async () => {
  const clock = scheduler();
  const results = ["failed", "failed", "stop"];
  let reads = 0;
  const follower = followTerminalOutput(async () => { reads++; return results.shift(); }, { ...clock, maxDelay: 1500 });
  follower.poke();
  await flush();
  assert.deepEqual(clock.due.map(item => item.ms), [1000]);
  clock.due.shift().run();
  await flush();
  assert.deepEqual(clock.due.map(item => item.ms), [1500], "capped");
  clock.due.shift().run();
  await flush();
  assert.equal(reads, 3);
  follower.poke();
  await flush();
  assert.equal(reads, 3, "a terminal that became another instance is not read again");
});

test("a push names its terminal and generation", () => {
  assert.equal(outputFor({ id: "t1", generation: "g1", data: "x" }, "t1", "g1"), true);
  assert.equal(outputFor({ id: "t1", generation: "g0", data: "x" }, "t1", "g1"), false);
  assert.equal(outputFor({ id: "t2", generation: "g1" }, "t1", "g1"), false);
  assert.equal(outputFor(null, "t1", "g1"), false);
});
