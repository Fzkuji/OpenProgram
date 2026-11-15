// The composer's bottom control row collapses its labels to icons when its
// full-size clusters do not fit. The full-size need is measured on a hidden
// copy of the row (the live row's Buttons may still be mid-transition), with
// hysteresis so the row does not flap at the boundary.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers } from "../../_support/dom.mjs";

installLoader();
const window = installDom();
const { createElement: h, useRef } = await import("react");
const { useCompactControlsRow } = await import("../../../components/chat/composer/controls/use-compact-controls-row.ts");

// Layout stubs: a cluster's width comes from its data-full / data-compact-w
// attributes depending on whether its row is compact; data-position marks
// out-of-flow children; the row's gap is 8px.
window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  const row = this.parentElement;
  const compact = row?.getAttribute("data-compact") === "true";
  const width = Number(this.getAttribute(compact ? "data-compact-w" : "data-full") ?? 0);
  return { left: 0, top: 0, right: width, bottom: 20, width, height: 20, x: 0, y: 0 };
};
// The row's available width comes from its data-available attribute.
Object.defineProperty(window.HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get() { return Number(this.getAttribute("data-available") ?? 0); },
});
globalThis.getComputedStyle = window.getComputedStyle = (element) => ({
  columnGap: element.classList?.contains("row") ? "8px" : "0px",
  position: element.getAttribute?.("data-position") ?? "static",
});
const fontListeners = new Set();
Object.defineProperty(document, "fonts", {
  configurable: true,
  value: {
    ready: Promise.resolve(),
    addEventListener: (type, fn) => type === "loadingdone" && fontListeners.add(fn),
    removeEventListener: (_type, fn) => fontListeners.delete(fn),
  },
});

const frame = () => new Promise((resolve) => setTimeout(resolve, 5));
let view;
afterEach(async () => { await view?.unmount(); view = undefined; });

/** Render a row of clusters (full widths) and give the row `available` px. */
async function mountRow(clusters, available) {
  function Row() {
    const ref = useRef(null);
    useCompactControlsRow(ref);
    return h("div", { ref, className: "row", "data-available": available }, clusters.map((props, i) => h("span", { key: i, ...props })));
  }
  view = await render(h(Row));
  const row = view.container.querySelector(".row");
  await view.act(frame);
  return {
    row,
    async resize(width) { row.setAttribute("data-available", width); await view.act(async () => { resizeObservers.trigger(row); await frame(); }); },
  };
}
const cluster = (full, compact = 20) => ({ "data-full": full, "data-compact-w": compact });

describe("useCompactControlsRow", () => {
  test("a row whose clusters fit stays full size", async () => {
    const { row } = await mountRow([cluster(100), cluster(100)], 300);
    assert.equal(row.hasAttribute("data-compact"), false);
  });

  test("a row whose clusters plus gaps overflow goes compact", async () => {
    const { row } = await mountRow([cluster(100), cluster(100)], 206);
    assert.equal(row.getAttribute("data-compact"), "true");
  });

  test("expanding again needs 12px of slack beyond the full-size need", async () => {
    const { row, resize } = await mountRow([cluster(100), cluster(100)], 150);
    assert.equal(row.getAttribute("data-compact"), "true");
    await resize(209);
    assert.equal(row.getAttribute("data-compact"), "true");
    await resize(221);
    assert.equal(row.hasAttribute("data-compact"), false);
  });

  test("the need is measured on a hidden full-size copy that is removed, never on the live row", async () => {
    const { row } = await mountRow([cluster(100), cluster(100)], 150);
    assert.equal(row.getAttribute("data-compact"), "true");
    // Re-measure while the live row is compact (its clusters report 20px).
    await view.act(async () => { row.firstChild.className = "changed"; await frame(); await frame(); });
    assert.equal(row.getAttribute("data-compact"), "true");
    assert.equal(row.hasAttribute("data-measuring"), false);
    assert.equal(document.querySelector("[data-measuring]"), null);
    assert.equal(row.parentElement.children.length, 1);
  });

  test("a class change inside the row (a tool toggled, a model switched) re-measures", async () => {
    const { row } = await mountRow([cluster(100), cluster(100)], 150);
    assert.equal(row.getAttribute("data-compact"), "true");
    await view.act(async () => {
      row.firstChild.setAttribute("data-full", "20");
      row.firstChild.className = "toggled-off";
      await frame(); await frame();
    });
    assert.equal(row.hasAttribute("data-compact"), false);
  });

  test("out-of-flow tips and cards do not count toward the need", async () => {
    const { row } = await mountRow(
      [cluster(100), cluster(100), { "data-full": 400, "data-position": "fixed" }, { "data-full": 400, "data-position": "absolute" }],
      220,
    );
    assert.equal(row.hasAttribute("data-compact"), false);
  });

  test("a web font finishing loading re-measures the labels", async () => {
    const { row } = await mountRow([cluster(100), cluster(100)], 220);
    assert.equal(row.hasAttribute("data-compact"), false);
    row.firstChild.setAttribute("data-full", "200");
    await view.act(async () => { for (const fn of fontListeners) fn(); await frame(); });
    assert.equal(row.getAttribute("data-compact"), "true");
  });

  test("a resize is judged in the observer, with no frame, and the flag is written only when it changes", async () => {
    const { row } = await mountRow([cluster(100), cluster(100)], 300);
    const writes = [];
    const observer = new window.MutationObserver((records) => { for (const r of records) writes.push(r.attributeName); });
    observer.observe(row, { attributes: true, attributeFilter: ["data-compact"] });
    const raf = window.requestAnimationFrame;
    let frames = 0;
    window.requestAnimationFrame = globalThis.requestAnimationFrame = (fn) => { frames += 1; return raf(fn); };
    try {
      // A pane narrowing past the need and on: one change, then none.
      for (const width of [250, 200, 190, 180, 170]) {
        row.setAttribute("data-available", width);
        resizeObservers.trigger(row);
        assert.equal(row.getAttribute("data-compact") === "true", width < 207, `decided at ${width}px in the observer`);
      }
      await view.act(frame);
      assert.equal(frames, 0, "no frame was asked for");
      assert.deepEqual(writes, ["data-compact"], "set once while it stayed compact");
    } finally {
      window.requestAnimationFrame = globalThis.requestAnimationFrame = raf;
      observer.disconnect();
    }
  });

  test("unmounting clears the compact flag", async () => {
    const { row } = await mountRow([cluster(300)], 100);
    assert.equal(row.getAttribute("data-compact"), "true");
    await view.unmount();
    view = undefined;
    assert.equal(row.hasAttribute("data-compact"), false);
  });
});
