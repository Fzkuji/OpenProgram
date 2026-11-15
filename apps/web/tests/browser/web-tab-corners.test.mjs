import test from "node:test";
import assert from "node:assert/strict";
import { isTransparent, measureWebTabCorners } from "../../lib/browser/web-tab/web-tab-bounds.ts";

function el(rect, style = {}, parentElement = null) {
  return {
    parentElement,
    style: { borderBottomLeftRadius: "0px", overflow: "visible", backgroundColor: "rgba(0, 0, 0, 0)", boxShadow: "none", ...style },
    getBoundingClientRect: () => ({ left: rect[0], top: rect[1], right: rect[0] + rect[2], bottom: rect[1] + rect[3] }),
  };
}

test.beforeEach(() => { globalThis.getComputedStyle = (node) => node.style; });

test("a page flush with the rounded card reports the card's radius, shadow and the colour behind it", () => {
  const app = el([0, 40, 1656, 889], { backgroundColor: "oklch(0.965 0.0076 264.88)" });
  const card = el([288, 47, 1080, 875], { borderBottomLeftRadius: "12px", overflow: "hidden", boxShadow: "rgba(0, 0, 0, 0.1) 0px 1px 3px 0px", backgroundColor: "oklch(0.99 0 0)" }, app);
  const pane = el([288, 47, 1080, 875], { overflow: "hidden" }, card);
  const frame = el([288, 120, 1080, 802], {}, pane);
  assert.deepEqual(measureWebTabCorners(frame), {
    radius: 12, outside: "oklch(0.965 0.0076 264.88)", shadow: "rgba(0, 0, 0, 0.1) 0px 1px 3px 0px",
  });
});

test("a page inset from the rounded card has no corners to paint", () => {
  const card = el([288, 47, 1080, 875], { borderBottomLeftRadius: "12px", overflow: "hidden" }, el([0, 0, 1656, 929], { backgroundColor: "rgb(1, 2, 3)" }));
  assert.equal(measureWebTabCorners(el([294, 120, 1068, 796], {}, card)), null);
  assert.equal(measureWebTabCorners(el([0, 0, 10, 10])), null);
});

test("transparent colours are skipped in any notation", () => {
  assert.deepEqual(["transparent", "rgba(0, 0, 0, 0)", "oklch(0.99 0 0 / 0)", "oklch(0.965 0.0076 264.88)", "rgb(1, 2, 3)"].map(isTransparent),
    [true, true, true, false, false]);
});
