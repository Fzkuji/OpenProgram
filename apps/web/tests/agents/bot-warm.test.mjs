import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
    return nextResolve(specifier, context);
  },
});

const { BOT_SHAPES } = await import("../../components/avatar/bot/catalog.ts");
const { resetWarming, shapeReady, startWarming, wantShape, watchShapes } = await import("../../components/avatar/bot/warm.ts");
const { loadBotRuntime } = await import("../../components/avatar/bot/runtime.ts");

/** Timers and idle callbacks that run only when told, in order. */
function fakeScheduler() {
  const tasks = [];
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (run) => { tasks.push({ run, kind: "task" }); return tasks.length; };
  globalThis.requestIdleCallback = (run) => { tasks.push({ run, kind: "idle" }); return tasks.length; };
  return {
    tasks,
    step() { const next = tasks.shift(); next.run(); return next.kind; },
    restore() { globalThis.setTimeout = realSetTimeout; delete globalThis.requestIdleCallback; },
  };
}

function fakeRuntime() {
  const prepared = [];
  return { prepared, runtime: { prepare: (shape) => prepared.push(shape) } };
}

test("shapes are prepared one per task: wanted ones first, the rest in idle time", () => {
  resetWarming();
  const clock = fakeScheduler();
  try {
    const { runtime, prepared } = fakeRuntime();
    wantShape("rocket");
    wantShape("ghost");
    assert.equal(clock.tasks.length, 0, "nothing runs before the renderer is in");
    startWarming(runtime);
    assert.equal(clock.step(), "task");
    assert.equal(clock.step(), "task");
    assert.deepEqual(prepared, ["rocket", "ghost"]);
    assert.equal(clock.step(), "idle");
    assert.equal(clock.tasks.length, 1, "one shape at a time");
    // A shape wanted meanwhile jumps the idle queue.
    wantShape("witch-hat");
    while (clock.tasks.length) clock.step();
    assert.equal(prepared.indexOf("witch-hat"), 3);
    assert.deepEqual([...prepared].sort(), [...BOT_SHAPES].sort());
    for (const shape of BOT_SHAPES) assert.equal(shapeReady(shape), true);
  } finally {
    clock.restore();
  }
});

test("a prepared shape tells its watchers, and a failing one does not hold its glyph up", () => {
  resetWarming();
  const clock = fakeScheduler();
  try {
    let told = 0;
    const stop = watchShapes(() => { told += 1; });
    startWarming({ prepare: (shape) => { if (shape === "chat") throw new Error("bad"); } });
    clock.step();
    assert.equal(shapeReady("chat"), true);
    assert.equal(told, 1);
    stop();
    clock.step();
    assert.equal(told, 1);
  } finally {
    clock.restore();
  }
});

test("preparing a shape is what makes the renderer's first drawing of it cheap", async () => {
  const runtime = await loadBotRuntime();
  const { renderAvatarSvg } = await import("open-bot-avatar");
  const definition = runtime.definition("barbell");
  const t0 = performance.now();
  runtime.prepare("barbell");
  const cold = performance.now() - t0;
  const t1 = performance.now();
  renderAvatarSvg(definition, { reducedMotion: true });
  const warm = performance.now() - t1;
  assert.ok(warm < cold / 3, `cold ${cold.toFixed(1)} ms, warm ${warm.toFixed(1)} ms`);
});
