import assert from "node:assert/strict";
import test from "node:test";
import { observeSwipe, rubberBand, springTo, swipeTurns, tweenTo } from "../../components/right-sidebar/running/sideways.ts";

/** One wheel event as a trackpad sends it. */
function wheel(target, deltaX, deltaY = 0) {
  const event = new Event("wheel", { cancelable: true });
  Object.assign(event, { deltaX, deltaY, deltaZ: 0, deltaMode: 0 });
  target.dispatchEvent(event);
  return event;
}

function watch(own, accepts = () => true) {
  const target = new EventTarget();
  const seen = [];
  const stop = observeSwipe(target, {
    begin: direction => { seen.push(["begin", direction]); return accepts(direction); },
    move: moved => seen.push(["move", moved]),
    end: moved => seen.push(["end", moved]),
    notch: direction => seen.push(["notch", direction]),
  }, own);
  return { target, seen, stop };
}

test("a swipe turns on distance or on a flick the way it was going", () => {
  assert.equal(swipeTurns(60, 0, 48), true);
  assert.equal(swipeTurns(30, 0, 48), false);
  assert.equal(swipeTurns(30, 900, 48), true, "short and fast is a flick");
  assert.equal(swipeTurns(30, -900, 48), false, "a flick back does not turn");
  assert.equal(swipeTurns(-30, -900, 48), true);
});

test("a pull past an end gives less and less, never more than its limit", () => {
  assert.ok(rubberBand(20) < 20);
  assert.ok(rubberBand(10000) < 44);
  assert.equal(rubberBand(-50), -rubberBand(50));
});

test("with no screen a spring lands at once", () => {
  const seen = [];
  let done = false;
  springTo(0, 256, 0, value => seen.push(value), () => { done = true; });
  assert.deepEqual(seen, [256]);
  assert.equal(done, true);
});

test("with no screen a move between columns lands at once, in one step", () => {
  const seen = [];
  let done = 0;
  tweenTo(0, -256, 240, value => seen.push(value), () => { done += 1; });
  assert.deepEqual(seen, [-256]);
  assert.equal(done, 1);
});

test("a sideways gesture is followed from where it became one, and up-and-down scrolling is left alone", t => {
  const { target, seen, stop } = watch(true);
  t.after(stop);
  assert.equal(wheel(target, 0, 12).defaultPrevented, false, "the browser scrolls up and down");
  assert.deepEqual(seen, []);
  assert.equal(wheel(target, 4, 1).defaultPrevented, true, "the chart never scrolls sideways by itself");
  assert.deepEqual(seen, [], "too little to be a swipe yet");
  wheel(target, 5, 0);
  assert.deepEqual(seen, [["begin", 1], ["move", 9]]);
  wheel(target, 10, 0);
  assert.deepEqual(seen.at(-1), ["move", 19]);
  assert.equal(wheel(target, 1, 8).defaultPrevented, true, "once followed, the gesture is all the swipe's");
});

test("a gesture nobody takes stays the browser's", t => {
  const { target, seen, stop } = watch(false, () => false);
  t.after(stop);
  assert.equal(wheel(target, -8, 0).defaultPrevented, false);
  assert.deepEqual(seen, [["begin", -1]]);
  assert.equal(wheel(target, -8, 0).defaultPrevented, false);
  assert.deepEqual(seen, [["begin", -1]], "asked once a gesture");
});

test("one step of a mouse wheel turned sideways is a notch, not a swipe", t => {
  const { target, seen, stop } = watch(true);
  t.after(stop);
  wheel(target, 100, 0);
  assert.deepEqual(seen, [["notch", 1]]);
});
