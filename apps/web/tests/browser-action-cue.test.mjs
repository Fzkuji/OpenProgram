import assert from "node:assert/strict";
import test from "node:test";

import {
  clampFloatPosition,
  controlSurfaceVisible,
  cueTravelDurationMs,
  isStaleCueIdentity,
  lerpCuePoint,
  nextCueTravel,
} from "../lib/state/browser-action-cue.ts";

test("idle and closed hide the floating control", () => {
  assert.equal(controlSurfaceVisible("idle"), false);
  assert.equal(controlSurfaceVisible("closed"), false);
  assert.equal(controlSurfaceVisible("active"), true);
  assert.equal(controlSurfaceVisible("paused"), true);
});

test("first cue appears at the target; later cues travel then click", () => {
  const next = { x: 40, y: 80 };
  assert.deepEqual(nextCueTravel(null, next, false), {
    from: null,
    to: next,
    animateMove: false,
  });
  const last = { x: 10, y: 10 };
  assert.deepEqual(nextCueTravel(last, next, false), {
    from: last,
    to: next,
    animateMove: true,
  });
  assert.equal(nextCueTravel(last, next, true).animateMove, false);
  assert.ok(cueTravelDurationMs(last, next) >= 80);
  const mid = lerpCuePoint(last, next, 0.5);
  assert.ok(mid.x > last.x && mid.x < next.x);
});

test("stale generation and sequence cancel instead of replaying", () => {
  const last = { resourceId: "page-a", generation: 2, sequence: 4 };
  assert.equal(isStaleCueIdentity(last, { resourceId: "page-a", generation: 2, sequence: 4 }), true);
  assert.equal(isStaleCueIdentity(last, { resourceId: "page-a", generation: 1, sequence: 9 }), true);
  assert.equal(isStaleCueIdentity(last, { resourceId: "page-a", generation: 2, sequence: 5 }), false);
  assert.equal(isStaleCueIdentity(last, { resourceId: "page-b", generation: 1, sequence: 1 }), false);
});

test("float position stays inside the viewport", () => {
  const clamped = clampFloatPosition(9000, 9000, { width: 36, height: 36 }, { width: 200, height: 100 });
  assert.ok(clamped.left <= 200 - 36 - 4);
  assert.ok(clamped.top <= 100 - 36 - 4);
});
