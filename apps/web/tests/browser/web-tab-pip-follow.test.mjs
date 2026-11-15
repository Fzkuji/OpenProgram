/**
 * The floating preview's saved place follows its pane's box: it keeps its
 * distance to the side it was placed nearer, is fitted inside, and one box
 * change moves it once however many previews report it. The place is
 * derived from the placement and the current box only, so any path of
 * boxes that ends where it started leaves the preview where it was.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

installLoader();
installDom({ pathname: "/chat" });

const { followPipBox, useWebTabPip } = await import("../../lib/browser/web-tab/web-tab-pip-store.ts");

const PANE = { x: 22, y: 22, width: 956, height: 756 };
const WIDER = { ...PANE, width: 1236 };
const CORNER = { x: 678, y: 22, width: 300, height: 198.75 };
const place = () => useWebTabPip.getState().rect;

test("the first box is only remembered", () => {
  useWebTabPip.setState({ rect: CORNER, rectBox: null });
  followPipBox(PANE, true);
  assert.deepEqual(place(), CORNER);
  assert.deepEqual(useWebTabPip.getState().rectBox, PANE);
});

test("the sidebar closing leaves a right-hand preview where it is on screen", () => {
  followPipBox(WIDER, true);
  assert.deepEqual(place(), { ...CORNER, x: 958 });
  // A second preview reporting the same box does not move it again.
  followPipBox(WIDER, true);
  assert.deepEqual(place(), { ...CORNER, x: 958 });
  followPipBox(PANE, true);
  assert.deepEqual(place(), CORNER);
});

test("a box too small for the place still fits it inside", () => {
  followPipBox({ ...PANE, width: 500 }, true);
  const rect = place();
  assert.ok(rect.x >= 22 && rect.x + rect.width <= 522);
});

test("a drag holds the place while the box is still remembered", () => {
  useWebTabPip.setState({ rect: CORNER, rectBox: PANE });
  followPipBox(WIDER, true, true);
  assert.deepEqual(place(), CORNER);
  assert.deepEqual(useWebTabPip.getState().rectBox, WIDER);
});

test("without a saved place there is nothing to move", () => {
  useWebTabPip.setState({ rect: null, rectBox: PANE });
  followPipBox(WIDER, true);
  assert.equal(place(), null);
  assert.deepEqual(useWebTabPip.getState().rectBox, WIDER);
});

// The right panel hiding and showing again: the pane grows by 288px to the
// right over a 200ms animation and shrinks back, reported frame by frame.
// On the way back one frame reports the pane 6px narrower than where it
// comes to rest.
const REST = { x: 22, y: 22, width: 1838, height: 756 };
// Pulled out of the dock about midway: its centre (939) is 2px left of the
// pane's (941), so it was placed nearer the left side.
const MIDWAY = { x: 789, y: 300, width: 300, height: 198.75 };
const frames = (from, to, steps) => Array.from({ length: steps }, (_, i) => ({
  ...REST, width: from + ((to - from) * (i + 1)) / steps,
}));
const HIDE_PANEL = frames(1838, 2126, 12);
const SHOW_PANEL = [...frames(2126, 1832, 11), { ...REST }];

function place_(rect, box) {
  useWebTabPip.setState({ rect, rectBox: box, placement: null });
}

function run(boxes, clamp = true) {
  for (const box of boxes) followPipBox(box, clamp);
  return place();
}

test("hiding and showing the right panel returns a midway preview to its place", () => {
  place_(MIDWAY, REST);
  followPipBox(REST, true);
  run(HIDE_PANEL);
  // Placed nearer the left, it stays put while the pane widens to the right.
  assert.deepEqual(place(), MIDWAY);
  // The 6px narrower frame on the way back puts the pane's centre left of
  // the preview's; a side re-decided there carried it 6px right (789 -> 795).
  assert.deepEqual(run(SHOW_PANEL), MIDWAY);
  assert.deepEqual(run([...HIDE_PANEL, ...SHOW_PANEL]), MIDWAY);
});

test("the place depends on the pane box only, not on the path the box took", () => {
  const placements = [
    { x: 789, y: 300, width: 300, height: 198.75 },
    { x: 1500, y: 40, width: 320, height: 210 },
    { x: 30, y: 560, width: 300, height: 198.75 },
    { x: 940, y: 300, width: 300, height: 198.75 },
  ];
  // A deterministic pseudo-random walk of boxes: both edges move, steps
  // differ out and back, some boxes are too small for the preview.
  let seed = 7;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const walk = () => Array.from({ length: 40 }, () => ({
    x: 22 + Math.round(random() * 300),
    y: 22 + Math.round(random() * 80),
    width: 200 + random() * 2000,
    height: 140 + random() * 800,
  }));
  for (const rect of placements) {
    for (let round = 0; round < 6; round += 1) {
      place_(rect, REST);
      const path = walk();
      const seen = new Map();
      for (const box of path) {
        followPipBox(box, true);
        const key = JSON.stringify(box);
        // The same box always gives the same place, whatever came before.
        if (seen.has(key)) assert.deepEqual(place(), seen.get(key));
        seen.set(key, place());
      }
      followPipBox(REST, true);
      assert.deepEqual(place(), rect, `round ${round} from ${JSON.stringify(rect)}`);
    }
  }
});

test("a pane too small for the preview does not shrink or move it for good", () => {
  const right = { x: 1500, y: 500, width: 320, height: 210 };
  place_(right, REST);
  run([{ ...REST, width: 900 }, { ...REST, width: 260, height: 300 }, { ...REST, width: 1200 }]);
  assert.deepEqual(run([REST]), right);
  const left = { x: 40, y: 40, width: 400, height: 255 };
  place_(left, REST);
  run([{ ...REST, x: 400, width: 300 }, { ...REST, height: 150 }]);
  assert.deepEqual(run([REST]), left);
});

test("the side is the one it was placed nearer while the left sidebar moves", () => {
  // Placed nearer the right: it stays the same distance from the right side
  // however far the left edge travels and comes back.
  const right = { x: 1400, y: 300, width: 300, height: 198.75 };
  place_(right, REST);
  const sidebar = (dx) => ({ ...REST, x: REST.x + dx, width: REST.width - dx });
  for (const dx of [40, 400, 900, 1200, 300]) {
    const rect = run([sidebar(dx)]);
    if (rect.width === right.width) assert.equal(rect.x, right.x);
  }
  assert.deepEqual(run([REST]), right);
  // Placed nearer the left: it moves with the left edge and comes back.
  const left = { x: 300, y: 300, width: 300, height: 198.75 };
  place_(left, REST);
  assert.equal(run([sidebar(200)]).x, 500);
  assert.deepEqual(run([sidebar(700), sidebar(50), REST]), left);
});

test("a held drag keeps the placement, and the drop is the new placement", () => {
  place_(MIDWAY, REST);
  followPipBox(REST, true);
  for (const box of HIDE_PANEL) followPipBox(box, true, true);
  assert.deepEqual(place(), MIDWAY);
  // Let go without moving it: the placement still stands for the new box.
  assert.deepEqual(run(SHOW_PANEL), MIDWAY);
  // Dropped in the wide pane nearer the right: that is where it now belongs.
  const wide = HIDE_PANEL.at(-1);
  run([wide]);
  followPipBox(wide, true, true);
  const dropped = { ...MIDWAY, x: 1700 };
  useWebTabPip.getState().setRect(dropped);
  assert.deepEqual(run(SHOW_PANEL), { ...dropped, x: 1700 - 288 });
  assert.deepEqual(run(HIDE_PANEL), dropped);
});

test("an expanded preview follows unclamped and returns as well", () => {
  place_(MIDWAY, REST);
  run([{ ...REST, width: 400 }, ...SHOW_PANEL], false);
  assert.deepEqual(run([REST], false), MIDWAY);
  // Collapsing again in a small pane and growing back: still its place.
  run([{ ...REST, width: 400 }], true);
  assert.deepEqual(run([REST], true), MIDWAY);
});

test("a place written before the first measurement is placed in the first box", () => {
  useWebTabPip.setState({ rect: MIDWAY, rectBox: null, placement: null });
  followPipBox(REST, true);
  assert.deepEqual(useWebTabPip.getState().placement?.box, REST);
  assert.deepEqual(run([...HIDE_PANEL, ...SHOW_PANEL]), MIDWAY);
});
