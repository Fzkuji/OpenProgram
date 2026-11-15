import test from "node:test";
import { readCenterTabStyles } from "../../scripts/testing/feature-source.mjs";
import assert from "node:assert/strict";
import {
  PIP_CONTENT_ASPECT,
  PIP_HEADER_HEIGHT,
  PIP_RESIZE_DIRS,
  anchorPipRect,
  clampPipRectAspect,
  pipContentAspect,
  placePipRect,
  resizePipRect,
} from "../../lib/browser/web-tab/web-tab-pip-geometry.ts";

const HEADER = PIP_HEADER_HEIGHT;
const MIN_W = 240;
const MIN_H = 160;
const ROOM = { x: 0, y: 0, width: 2000, height: 2000 };
const DEFAULT = { x: 40, y: 50, width: 400, height: 255 };
const EXPANDED = { x: 40, y: 50, width: 720, height: 435 };
const ORIGIN = { x: 400, y: 300, width: 400, height: 255 };

const GROW = {
  n: { dx: 0, dy: -80 },
  ne: { dx: 80, dy: -45 },
  e: { dx: 80, dy: 0 },
  se: { dx: 80, dy: 45 },
  s: { dx: 0, dy: 80 },
  sw: { dx: -80, dy: 45 },
  w: { dx: -80, dy: 0 },
  nw: { dx: -80, dy: -45 },
};

function resize(origin, dx, dy, box = ROOM, dir = "se") {
  return resizePipRect(origin, dx, dy, box, MIN_W, MIN_H, dir);
}

function clamp(rect, box) {
  return clampPipRectAspect(rect, box, MIN_W, MIN_H);
}

function contentAspect(rect) {
  return rect.width / (rect.height - HEADER);
}

function right(rect) { return rect.x + rect.width; }
function bottom(rect) { return rect.y + rect.height; }
function midX(rect) { return rect.x + rect.width / 2; }
function midY(rect) { return rect.y + rect.height / 2; }

function assertFiniteRect(rect) {
  for (const key of ["x", "y", "width", "height"]) {
    assert.equal(Number.isFinite(rect[key]), true, `${key} must be finite`);
  }
  assert.ok(rect.width >= 0);
  assert.ok(rect.height >= 0);
}

function assertInside(rect, box) {
  assert.ok(rect.x >= box.x - 1e-9);
  assert.ok(rect.y >= box.y - 1e-9);
  assert.ok(right(rect) <= box.x + box.width + 1e-9);
  assert.ok(bottom(rect) <= box.y + box.height + 1e-9);
}

function assertRatio(rect, aspect = PIP_CONTENT_ASPECT) {
  if (rect.height <= HEADER) return;
  assert.ok(Math.abs(contentAspect(rect) - aspect) < 1e-9, `${contentAspect(rect)} != ${aspect}`);
}

function assertOpposite(origin, next, dir) {
  if (dir === "e" || dir === "se" || dir === "ne") {
    assert.ok(Math.abs(next.x - origin.x) < 1e-9, `${dir} left`);
  }
  if (dir === "w" || dir === "sw" || dir === "nw") {
    assert.ok(Math.abs(right(next) - right(origin)) < 1e-9, `${dir} right`);
  }
  if (dir === "n" || dir === "s") {
    assert.ok(Math.abs(midX(next) - midX(origin)) < 1e-9, `${dir} midX`);
  }
  if (dir === "s" || dir === "se" || dir === "sw") {
    assert.ok(Math.abs(next.y - origin.y) < 1e-9, `${dir} top`);
  }
  if (dir === "n" || dir === "ne" || dir === "nw") {
    assert.ok(Math.abs(bottom(next) - bottom(origin)) < 1e-9, `${dir} bottom`);
  }
  if (dir === "e" || dir === "w") {
    assert.ok(Math.abs(midY(next) - midY(origin)) < 1e-9, `${dir} midY`);
  }
}

function projectedWidth(origin, dx, dy) {
  const aspect = pipContentAspect(origin);
  return origin.width + (dx + dy / aspect) / (1 + 1 / (aspect * aspect));
}

test("the PiP header in CSS is as tall as the geometry assumes", () => {
  // Header height must equal PIP_HEADER_HEIGHT or the 16:9 page view
  // letterboxes and leaves a strip down the right edge.
  assert.match(readCenterTabStyles(), new RegExp(`\\.webPipChrome \\{[^}]*height: ${PIP_HEADER_HEIGHT}px`));
});

test("default and expanded windows are 16:9 content plus a 30px header", () => {
  assert.equal(HEADER, 30);
  assert.equal(DEFAULT.height - HEADER, 225);
  assert.equal(EXPANDED.height - HEADER, 405);
  assertRatio(DEFAULT);
  assertRatio(EXPANDED);
});

test("each of the eight directions grows, keeps ratio, and holds the opposite anchor", () => {
  for (const dir of PIP_RESIZE_DIRS) {
    const { dx, dy } = GROW[dir];
    const next = resize(ORIGIN, dx, dy, ROOM, dir);
    assertFiniteRect(next);
    assertRatio(next);
    assertOpposite(ORIGIN, next, dir);
    assert.ok(next.width > ORIGIN.width, dir);
    assert.ok(next.height > ORIGIN.height, dir);
    assertInside(next, ROOM);
  }
});

test("each of the eight directions shrinks inward without moving the opposite anchor", () => {
  for (const dir of PIP_RESIZE_DIRS) {
    const { dx, dy } = GROW[dir];
    const next = resize(ORIGIN, -dx, -dy, ROOM, dir);
    assertRatio(next);
    assertOpposite(ORIGIN, next, dir);
    assert.ok(next.width < ORIGIN.width, dir);
    assert.ok(next.height < ORIGIN.height, dir);
  }
});

test("omitted direction stays bottom-right for compatibility", () => {
  const explicit = resize(DEFAULT, 80, 45, ROOM, "se");
  const implied = resizePipRect(DEFAULT, 80, 45, ROOM, MIN_W, MIN_H);
  assert.deepEqual(implied, explicit);
  assert.equal(implied.x, DEFAULT.x);
  assert.equal(implied.y, DEFAULT.y);
});

test("horizontal se drag resizes both axes and keeps the top-left", () => {
  const next = resize(DEFAULT, 80, 0);
  assert.equal(next.x, DEFAULT.x);
  assert.equal(next.y, DEFAULT.y);
  assert.ok(next.width > DEFAULT.width);
  assert.ok(next.height > DEFAULT.height);
  assert.ok(Math.abs(next.width - projectedWidth(DEFAULT, 80, 0)) < 1e-9);
  assertRatio(next);
});

test("vertical se drag resizes both axes and keeps the top-left", () => {
  const next = resize(DEFAULT, 0, 90);
  assert.equal(next.x, DEFAULT.x);
  assert.equal(next.y, DEFAULT.y);
  assert.ok(next.width > DEFAULT.width);
  assert.ok(next.height > DEFAULT.height);
  assertRatio(next);
});

test("east and west edges are width-driven; north and south are height-driven", () => {
  const east = resize(ORIGIN, 80, 40, ROOM, "e");
  const west = resize(ORIGIN, -80, 40, ROOM, "w");
  const south = resize(ORIGIN, 40, 80, ROOM, "s");
  const north = resize(ORIGIN, 40, -80, ROOM, "n");
  assert.equal(east.width, ORIGIN.width + 80);
  assert.equal(west.width, ORIGIN.width + 80);
  assert.equal(south.height, ORIGIN.height + 80);
  assert.equal(north.height, ORIGIN.height + 80);
  assertRatio(east);
  assertRatio(west);
  assertRatio(south);
  assertRatio(north);
  assertOpposite(ORIGIN, east, "e");
  assertOpposite(ORIGIN, west, "w");
  assertOpposite(ORIGIN, south, "s");
  assertOpposite(ORIGIN, north, "n");
});

test("content-diagonal se drag is monotonic and ratio-preserving", () => {
  let previous = DEFAULT.width;
  for (let t = 0.1; t <= 1; t += 0.1) {
    const next = resize(DEFAULT, 160 * t, 90 * t);
    assert.ok(next.width >= previous - 1e-9);
    assert.equal(next.x, DEFAULT.x);
    assert.equal(next.y, DEFAULT.y);
    assertRatio(next);
    previous = next.width;
  }
});

test("opposing dx/dy on corners is continuous instead of switching grow and shrink", () => {
  for (const dir of ["se", "nw", "ne", "sw"]) {
    let previous = resize(ORIGIN, 100, -80, ROOM, dir);
    for (let dy = -79; dy <= 80; dy += 1) {
      const next = resize(ORIGIN, 100, dy, ROOM, dir);
      assert.ok(Math.abs(next.width - previous.width) < 1, dir);
      assertRatio(next);
      previous = next;
    }
  }
});

test("min clamp is 240x160 from every handle when the parent allows", () => {
  for (const dir of PIP_RESIZE_DIRS) {
    const { dx, dy } = GROW[dir];
    const next = resize(ORIGIN, -dx * 20, -dy * 20, ROOM, dir);
    assert.equal(next.width, MIN_W, dir);
    assert.ok(next.height >= MIN_H, dir);
    assertRatio(next);
    assertOpposite(ORIGIN, next, dir);
  }
});

test("max clamp fits both parent axes from corners and edges", () => {
  const box = { x: 0, y: 0, width: 900, height: 700 };
  const origin = { x: 200, y: 150, width: 400, height: 255 };
  for (const dir of PIP_RESIZE_DIRS) {
    const { dx, dy } = GROW[dir];
    const next = resize(origin, dx * 20, dy * 20, box, dir);
    assertInside(next, box);
    assertRatio(next);
    assertOpposite(origin, next, dir);
    assert.ok(next.width > origin.width || next.height > origin.height, dir);
    assert.ok(next.width < box.width || next.height < box.height, dir);
  }
});

test("narrow parent shrinks below 240 wide rather than overflowing", () => {
  const box = { x: 0, y: 0, width: 200, height: 800 };
  const next = clamp({ x: 0, y: 0, width: 400, height: 255 }, box);
  assert.equal(next.width, 200);
  assert.ok(next.width < MIN_W);
  assertRatio(next);
  assertInside(next, box);
});

test("tall parent shrinks below 160 tall rather than overflowing", () => {
  const box = { x: 0, y: 0, width: 800, height: 120 };
  const next = clamp({ x: 0, y: 0, width: 400, height: 255 }, box);
  assert.equal(next.height, 120);
  assert.ok(next.height < MIN_H);
  assertRatio(next);
  assertInside(next, box);
});

test("tiny parent still fits, even below the usual minimum", () => {
  const box = { x: 10, y: 20, width: 120, height: 90 };
  const next = clamp({ x: 10, y: 20, width: 400, height: 255 }, box);
  assertInside(next, box);
  assertFiniteRect(next);
  assertRatio(next);
});

test("parent shorter than the header stays finite and inside bounds", () => {
  const box = { x: 0, y: 0, width: 200, height: 20 };
  const next = clamp({ x: 0, y: 0, width: 400, height: 255 }, box);
  assertInside(next, box);
  assertFiniteRect(next);
  assert.equal(next.height, 20);
});

test("west resize against the left wall keeps the right edge and fits", () => {
  const box = { x: 0, y: 0, width: 500, height: 800 };
  const origin = { x: 80, y: 100, width: 200, height: 200 * 9 / 16 + HEADER };
  const next = resize(origin, -200, 0, box, "w");
  assert.ok(Math.abs(right(next) - right(origin)) < 1e-9);
  assert.equal(next.x, box.x);
  assertRatio(next);
  assertInside(next, box);
});

test("default and expanded auto-clamp stay ratio-preserving, not independently clipped", () => {
  const box = { x: 0, y: 0, width: 300, height: 1000 };
  const def = clamp({ x: 80, y: 90, width: 400, height: 255 }, box);
  const exp = clamp({ x: 80, y: 90, width: 720, height: 435 }, box);
  assert.equal(def.width, 300);
  assert.notEqual(def.height, 255);
  assert.equal(exp.width, 300);
  assertRatio(def);
  assertRatio(exp);
  assertInside(def, box);
  assertInside(exp, box);
});

test("layout clamp may move the origin after a ratio-preserving shrink", () => {
  const box = { x: 0, y: 0, width: 500, height: 400 };
  const next = clamp({ x: 400, y: 300, width: 400, height: 255 }, box);
  assertInside(next, box);
  assertRatio(next);
  assert.equal(next.x, box.x + box.width - next.width);
  assert.equal(next.y, box.y + box.height - next.height);
});

test("fractional pointer deltas stay finite and keep the content ratio", () => {
  for (const dir of PIP_RESIZE_DIRS) {
    const next = resize(ORIGIN, 10.5, -3.25, ROOM, dir);
    assertFiniteRect(next);
    assertRatio(next);
    assertOpposite(ORIGIN, next, dir);
  }
});

test("custom origin ratio is kept instead of resetting to 16:9", () => {
  const origin = { x: 200, y: 200, width: 320, height: HEADER + 180 };
  assert.equal(pipContentAspect(origin), 320 / 180);
  const next = resize(origin, 64, 0, ROOM, "e");
  assert.ok(next.width > origin.width);
  assertRatio(next, 320 / 180);
  assertOpposite(origin, next, "e");
});

// A pane box in its parent's coordinates: the left sidebar closing moves the
// parent's left edge out, so the box keeps its x and grows by the sidebar.
const PANE = { x: 22, y: 22, width: 956, height: 756 };
const WIDER = { ...PANE, width: PANE.width + 280 };

test("a window by the right edge keeps its distance to it when the box grows leftward", () => {
  const rect = { x: 678, y: 22, width: 300, height: 198.75 };
  const moved = anchorPipRect(rect, PANE, WIDER);
  // +280 in a parent whose origin moved 280 to the left: still on screen.
  assert.deepEqual(moved, { ...rect, x: 958 });
  assert.equal(WIDER.x + WIDER.width - (moved.x + moved.width), PANE.x + PANE.width - (rect.x + rect.width));
  assert.deepEqual(anchorPipRect(moved, WIDER, PANE), rect);
});

test("a window by the left edge keeps its distance to that edge instead", () => {
  const rect = { x: 40, y: 500, width: 300, height: 198.75 };
  assert.deepEqual(anchorPipRect(rect, PANE, WIDER), rect);
  assert.deepEqual(anchorPipRect(rect, PANE, { ...PANE, x: 122, width: 856 }), { ...rect, x: 140 });
});

test("the same rule holds top and bottom", () => {
  const taller = { ...PANE, height: PANE.height + 100 };
  const low = { x: 40, y: 560, width: 300, height: 198.75 };
  const high = { x: 40, y: 30, width: 300, height: 198.75 };
  assert.deepEqual(anchorPipRect(low, PANE, taller), { ...low, y: 660 });
  assert.deepEqual(anchorPipRect(high, PANE, taller), high);
});

// The right panel hiding and showing: the pane widens by 288px to the right
// and narrows back, frame by frame; one frame on the way back is 6px
// narrower than where the pane comes to rest.
const REST = { x: 22, y: 22, width: 1838, height: 756 };
const MIDWAY = { x: 789, y: 300, width: 300, height: 198.75 };
const steps = (from, to, n) => Array.from({ length: n }, (_, i) => ({ ...REST, width: from + ((to - from) * (i + 1)) / n }));
const ROUND = [...steps(1838, 2126, 12), ...steps(2126, 1832, 11), REST];
const FIT = { minWidth: MIN_W, minHeight: MIN_H };

test("re-deciding the side from each previous box makes the place depend on the path", () => {
  // The preview's centre is 2px left of the pane's. Carried step by step,
  // the 6px narrower frame puts the pane's centre left of it, the side
  // flips to the right there, and the last frame carries it 6px right.
  let rect = MIDWAY;
  let box = REST;
  for (const next of ROUND) { rect = anchorPipRect(rect, box, next); box = next; }
  assert.equal(rect.x, 795);
});

test("a place derived from the placement returns exactly, on both axes and sides", () => {
  const placements = [
    MIDWAY,
    { x: 1500, y: 520, width: 320, height: 210 },
    { x: 30, y: 30, width: 400, height: 255 },
    { x: 1400, y: 30, width: 300, height: 198.75 },
  ];
  const paths = [
    ROUND,
    // Out in big steps, back in small ones, both edges moving.
    [{ ...REST, x: 300, width: 1560 }, { ...REST, x: 600, width: 1000, height: 400 }, ...steps(1000, 1838, 7)],
    // Through a pane too small for the preview.
    [{ ...REST, width: 200, height: 120 }, { ...REST, x: 900, width: 300 }, { ...REST, height: 100 }],
  ];
  for (const rect of placements) {
    const placement = { rect, box: REST };
    for (const path of paths) {
      for (const box of path) {
        const placed = placePipRect(placement, box, FIT);
        assertFiniteRect(placed);
        assertInside(placed, box);
      }
      assert.deepEqual(placePipRect(placement, REST, FIT), rect);
      assert.deepEqual(placePipRect(placement, REST, null), rect);
    }
  }
});

test("the side is the one the window was placed nearer, however the box moves", () => {
  const right = { x: 1400, y: 300, width: 300, height: 198.75 };
  const placement = { rect: right, box: REST };
  // The left edge travels past the window's centre: still right-anchored.
  const narrow = { ...REST, x: 1300, width: 560 };
  assert.equal(placePipRect(placement, narrow, null).x, right.x);
  const left = { rect: { x: 300, y: 300, width: 300, height: 198.75 }, box: REST };
  assert.equal(placePipRect(left, { ...REST, width: 400 }, null).x, 300);
  assert.equal(placePipRect(left, { ...REST, x: 222, width: 1638 }, null).x, 500);
});

test("without a fit the place is carried but not clamped", () => {
  const placement = { rect: { x: 1500, y: 520, width: 320, height: 210 }, box: REST };
  const small = { ...REST, width: 300, height: 200 };
  const placed = placePipRect(placement, small, null);
  assert.equal(placed.width, 320);
  assert.equal(placed.x, 1500 + 300 - 1838);
});
