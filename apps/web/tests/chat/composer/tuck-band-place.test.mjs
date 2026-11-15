// The tuck band (use-tucked-controls.ts) is a ring RIM_OUT px past the
// input box on every side, placed from the box's offsets when the box or
// its stack resizes. Only a side that moved is written: an unchanged value
// written again still reaches every MutationObserver on the page, and a
// pane narrowing past the column's width resizes the box every frame.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers } from "../../_support/dom.mjs";

installLoader();
const window = installDom();
const { createElement: h, useRef } = await import("react");
const { useTuckedControls } = await import("../../../components/chat/composer/controls/use-tucked-controls.ts");

const layout = { left: 24, top: 0, width: 600, height: 52 };
/** Reads of the box's layout and writes to the band's style, in order. */
const order = [];
for (const [prop, key] of [["offsetLeft", "left"], ["offsetTop", "top"], ["offsetWidth", "width"], ["offsetHeight", "height"]]) {
  Object.defineProperty(window.HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      if (!this.hasAttribute("data-box")) return 0;
      order.push(`read ${key}`);
      return layout[key];
    },
  });
}

function Composer() {
  const box = useRef(null);
  const tuck = useTuckedControls(box, true);
  return h("div", { className: "stack" },
    h("div", { ref: tuck.bandRef, "data-tuck-band": "" }),
    h("div", { ref: box, "data-box": "" }));
}

test("the band follows the box and writes only the sides that moved", async () => {
  const view = await render(h(Composer));
  const band = view.container.querySelector("[data-tuck-band]");
  const box = view.container.querySelector("[data-box]");
  assert.deepEqual([band.style.left, band.style.top, band.style.width, band.style.height], ["18px", "-6px", "612px", "64px"]);
  // Counts every write to the band's style, changed or not.
  const writes = [];
  const style = band.style;
  Object.defineProperty(band, "style", {
    configurable: true,
    value: new Proxy(style, {
      get: (target, key) => target[key],
      set: (target, key, value) => { writes.push(key); target[key] = value; return true; },
    }),
  });
  // The box narrows in place, then a resize that changes nothing.
  layout.width = 560;
  resizeObservers.trigger(box);
  resizeObservers.trigger(box);
  await view.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.equal(band.style.width, "572px");
  assert.equal(band.style.left, "18px");
  assert.deepEqual(writes, ["width"], "one write: the width");
  await view.unmount();
});

test("the band reads all of the box's layout before it writes a side", async () => {
  Object.assign(layout, { left: 24, top: 0, width: 600, height: 52 });
  const view = await render(h(Composer));
  const band = view.container.querySelector("[data-tuck-band]");
  const box = view.container.querySelector("[data-box]");
  const style = band.style;
  Object.defineProperty(band, "style", {
    configurable: true,
    value: new Proxy(style, {
      get: (target, key) => target[key],
      set: (target, key, value) => { order.push(`write ${String(key)}`); target[key] = value; return true; },
    }),
  });
  // A pane narrowing recentres the box: its left side and its width change.
  order.length = 0;
  Object.assign(layout, { left: 44, width: 560 });
  resizeObservers.trigger(box);
  const lastRead = order.findLastIndex((step) => step.startsWith("read"));
  const firstWrite = order.findIndex((step) => step.startsWith("write"));
  assert.ok(firstWrite > lastRead, `a read after a write lays the page out again inside the callback: ${order.join(", ")}`);
  assert.deepEqual([band.style.left, band.style.width], ["38px", "572px"]);
  await view.unmount();
});
