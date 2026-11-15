import assert from "node:assert/strict";
import test from "node:test";
import { axisFor, axisLabels, barOf, computeActivityGeometry, geometryVariables, percentOf, programAxis, tickOf } from "../../components/right-sidebar/running/activity-page-geometry.ts";

test("the default card shows exactly the left half", () => {
  assert.deepEqual(computeActivityGeometry(272), { V: 256, L: 256, R: 256, track: 528, P: 168, both: false });
});

test("the narrowest card keeps both halves the same width", () => {
  assert.deepEqual(computeActivityGeometry(224), { V: 208, L: 208, R: 208, track: 432, P: 120, both: false });
});

test("both halves fit from the width where the right half reaches its share", () => {
  assert.equal(computeActivityGeometry(543).both, false);
  assert.equal(computeActivityGeometry(544).both, true);
  assert.deepEqual(computeActivityGeometry(704), { V: 688, L: 256, R: 416, track: 688, P: 328, both: true });
});

test("the geometry is written as CSS variables", () => {
  assert.deepEqual(geometryVariables(computeActivityGeometry(272)), { "--V": "256px", "--L": "256px", "--R": "256px", "--track": "528px", "--P": "168px", "--pw": "272px" });
});

test("the axis is the smallest that covers finished runs and open events", () => {
  assert.deepEqual(axisFor([], []), { seconds: 60, divisions: 3 });
  assert.deepEqual(axisFor([60], []), { seconds: 60, divisions: 3 });
  assert.deepEqual(axisFor([104], []), { seconds: 180, divisions: 3 });
  assert.deepEqual(axisFor([104], [200]), { seconds: 300, divisions: 5 });
  assert.deepEqual(axisFor([400], []), { seconds: 600, divisions: 5 });
  assert.deepEqual(axisFor([1000], []), { seconds: 1800, divisions: 3 });
  assert.deepEqual(axisFor([9000], []), { seconds: 3600, divisions: 3 });
});

test("a program under a minute uses its own length", () => {
  assert.deepEqual(programAxis(3), { seconds: 3, divisions: 3 });
  assert.deepEqual(programAxis(4), { seconds: 6, divisions: 3 });
  assert.deepEqual(programAxis(0), { seconds: 3, divisions: 3 });
  assert.deepEqual(programAxis(90), { seconds: 180, divisions: 3 });
});

test("axis labels carry a unit on the last only", () => {
  assert.deepEqual(axisLabels({ seconds: 60, divisions: 3 }), ["0", "20", "40", "60s"]);
  assert.deepEqual(axisLabels({ seconds: 180, divisions: 3 }), ["0", "1", "2", "3 min"]);
  assert.deepEqual(axisLabels({ seconds: 300, divisions: 5 }), ["0", "1", "2", "3", "4", "5 min"]);
  assert.deepEqual(axisLabels({ seconds: 3600, divisions: 3 }), ["0", "20", "40", "60 min"]);
  assert.deepEqual(axisLabels({ seconds: 3, divisions: 3 }), ["0", "1", "2", "3s"]);
});

test("positions are percents clamped to the axis", () => {
  const axis = { seconds: 180, divisions: 3 };
  assert.equal(percentOf(90, axis), 50);
  assert.equal(percentOf(-5, axis), 0);
  assert.equal(percentOf(400, axis), 100);
  assert.deepEqual(tickOf(45, axis), { left: 25 });
});

test("a bar past the axis fills it with a round end; only an unknown end is flat", () => {
  const axis = { seconds: 180, divisions: 3 };
  assert.deepEqual(barOf(0, 90, axis), { left: 0, width: 50, flatEnd: false });
  assert.deepEqual(barOf(0, 900, axis), { left: 0, width: 100, flatEnd: false });
  assert.deepEqual(barOf(0, null, axis), { left: 0, width: 100, flatEnd: true });
  assert.deepEqual(barOf(45, null, axis), { left: 25, width: 75, flatEnd: true });
  assert.deepEqual(barOf(135, 900, axis), { left: 75, width: 25, flatEnd: false });
});
