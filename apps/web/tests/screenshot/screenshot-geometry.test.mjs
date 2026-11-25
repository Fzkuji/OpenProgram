// The screenshot overlay's selection and bars (lib/screenshot/geometry.ts),
// and the undo history and step numbers its marks share with the composer's
// image annotator (lib/annotate/marks.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";

installLoader({});
const { clampRect, handleAt, moveRect, placeBeside, placeToolbar, resizeRect, GAP } = await import("../../lib/screenshot/geometry.ts");
const { commit, emptyHistory, isEmpty, nextNumber, redo, undo } = await import("../../lib/annotate/marks.ts");

const screen = { x: 0, y: 0, width: 1000, height: 800 };
const bar = { width: 400, height: 40 };

test("the tools bar sits under the selection, above it without room, inside it without either", () => {
  assert.deepEqual(placeToolbar({ x: 100, y: 100, width: 300, height: 200 }, bar, screen), { x: 100, y: 300 + GAP, side: "below" });
  const low = { x: 100, y: 600, width: 300, height: 180 };
  assert.deepEqual(placeToolbar(low, bar, screen), { x: 100, y: 600 - GAP - 40, side: "above" });
  const whole = placeToolbar(screen, bar, screen);
  assert.equal(whole.side, "inside");
  assert.equal(whole.y, 800 - GAP - 40);
  // Kept on the screen at the right edge.
  assert.equal(placeToolbar({ x: 900, y: 100, width: 90, height: 50 }, bar, screen).x, 1000 - GAP - 400);
});

test("a panel goes right of the selection, else left, else inside", () => {
  const panel = { width: 300, height: 200 };
  assert.equal(placeBeside({ x: 100, y: 100, width: 200, height: 100 }, panel, screen).x, 300 + GAP);
  assert.equal(placeBeside({ x: 600, y: 100, width: 300, height: 100 }, panel, screen).x, 600 - GAP - 300);
  assert.equal(placeBeside(screen, panel, screen).x, 1000 - 300 - GAP);
});

test("handles, moving and resizing keep the selection on the screen", () => {
  const r = { x: 100, y: 100, width: 200, height: 100 };
  assert.equal(handleAt({ x: 101, y: 99 }, r, 6), "nw");
  assert.equal(handleAt({ x: 200, y: 200 }, r, 6), "s");
  assert.equal(handleAt({ x: 200, y: 150 }, r, 6), null);
  assert.deepEqual(moveRect(r, -500, 0, screen), { ...r, x: 0 });
  // Dragging the left edge past the right one flips the selection.
  assert.deepEqual(resizeRect(r, "w", 300, 0, screen), { x: 300, y: 100, width: 100, height: 100 });
  assert.deepEqual(clampRect({ x: -10, y: 790, width: 50, height: 50 }, screen), { x: 0, y: 790, width: 40, height: 10 });
});

test("undo and redo walk the marks; step numbers count up", () => {
  const a = { kind: "number", color: "#f00", width: 4, at: { x: 1, y: 1 }, n: 1 };
  const h1 = commit(emptyHistory, [a]);
  assert.equal(nextNumber(h1.present), 2);
  const h2 = commit(h1, [...h1.present, { ...a, n: 2 }]);
  assert.equal(undo(h2).present.length, 1);
  assert.equal(redo(undo(h2)).present.length, 2);
  assert.equal(commit(undo(h2), []).future.length, 0, "a new mark drops what could be redone");
  assert.equal(isEmpty({ kind: "rect", color: "#f00", width: 4, from: { x: 0, y: 0 }, to: { x: 1, y: 1 } }), true);
  assert.equal(isEmpty({ kind: "pen", color: "#f00", width: 4, points: [{ x: 0, y: 0 }, { x: 5, y: 5 }] }), false);
});
