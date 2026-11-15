// watchPosition reports an element's moves in the viewport from an
// IntersectionObserver whose root is shrunk to the element's own box, so it
// needs no measurement of its own, and sets itself up again after each move.
import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

globalThis.window = { innerWidth: 1000, innerHeight: 800 };
const observers = new Set();
globalThis.IntersectionObserver = class {
  constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); observers.add(this); }
  observe(target) { this.targets.add(target); }
  disconnect() { this.targets.clear(); observers.delete(this); }
};
const { watchPosition } = await import("../../lib/browser/web-tab/watch-position.ts");

let box;
const element = { getBoundingClientRect: () => ({ ...box }) };
/** Delivers what the browser would report for the element now. */
function deliver(ratio = 1) {
  for (const observer of [...observers]) {
    if (observer.targets.has(element)) observer.callback([{ boundingClientRect: { ...box }, intersectionRatio: ratio }], observer);
  }
}
const current = () => [...observers].find(observer => observer.targets.has(element));

beforeEach(() => { observers.clear(); box = { left: 100, top: 50, width: 300, height: 200 }; });

describe("watchPosition", () => {
  test("the root is the element's own box, in whole pixels", () => {
    box = { left: 100.5, top: 50.25, width: 300, height: 200 };
    const stop = watchPosition(element, () => {});
    // top, right, bottom, left insets from the window's edges, shrinking the root.
    assert.equal(current().options.rootMargin, "-50px -599px -549px -100px");
    assert.equal(current().options.threshold, 1);
    stop();
    assert.equal(observers.size, 0);
  });

  test("a move with no size change is reported once and watched again from there", () => {
    const moves = [];
    watchPosition(element, next => moves.push(next));
    deliver();
    assert.deepEqual(moves, [], "the first report of an element that has not moved");
    box = { ...box, top: 10 };
    deliver(0.8);
    assert.deepEqual(moves, [{ left: 100, top: 10, width: 300, height: 200 }]);
    assert.equal(current().options.rootMargin, "-10px -600px -590px -100px");
    deliver();
    assert.equal(moves.length, 1);
  });

  test("an element partly outside the window starts from the ratio it has, once", () => {
    box = { left: -100, top: 50, width: 300, height: 200 };
    const moves = [];
    watchPosition(element, next => moves.push(next));
    deliver(2 / 3);
    const tuned = current();
    assert.equal(tuned.options.threshold, 2 / 3);
    // A slightly different ratio on the next first report is not chased.
    deliver(0.6666);
    assert.equal(current(), tuned);
    assert.deepEqual(moves, []);
  });

  test("a box the caller already knows is where the watch starts", () => {
    const moves = [];
    watchPosition(element, next => moves.push(next), { left: 0, top: 0, width: 300, height: 200 });
    deliver();
    assert.equal(moves.length, 1, "the element was elsewhere by the first report");
  });
});
