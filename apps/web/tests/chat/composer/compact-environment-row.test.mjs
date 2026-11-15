/**
 * The environment row squeezes one level at a time (useCompactEnvironmentRow).
 *
 * linkedom has no layout, so the row's width and each chip's right edge are
 * stubbed: a chip's width depends on the squeeze level the hook has set on
 * the row, which is what CSS does in the app.
 */
import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers } from "../../_support/dom.mjs";

installLoader();
installDom();

const { createElement, useRef } = await import("react");
const { useCompactEnvironmentRow } = await import(
  "../../../components/chat/composer/environment-row/use-compact-environment-row.ts"
);

// Right edge of the last chip at squeeze levels 0, 1, 2 (level 3 is icons).
let needs;
let width;
let measuringSeen;
const styles = new WeakMap();
globalThis.getComputedStyle = window.getComputedStyle = (element) => styles.get(element) ?? { position: "static", paddingRight: "0" };

const frame = () => new Promise((resolve) => setTimeout(resolve, 0));

function Row({ tip = false }) {
  const ref = useRef(null);
  useCompactEnvironmentRow(ref);
  return createElement("div", { ref, "data-environment-row": "" },
    createElement("span", { className: "chip" }, "project"),
    createElement("span", { className: "chip last" }, "web"),
    tip ? createElement("span", { className: "tip" }, "hover tip") : null);
}

async function mount(props = {}) {
  const view = await render(createElement(Row, props));
  const row = view.container.querySelector("[data-environment-row]");
  Object.defineProperty(row, "clientWidth", { get: () => width });
  row.scrollLeft = 0;
  row.getBoundingClientRect = () => ({ left: 0, right: width, width, top: 0, bottom: 20, height: 20 });
  const level = () => Number(row.dataset.squeeze ?? 0);
  for (const chip of row.querySelectorAll(".chip")) {
    const isLast = chip.classList.contains("last");
    chip.getBoundingClientRect = () => {
      if (row.dataset.measuring) measuringSeen = true;
      const right = isLast ? needs[Math.min(level(), 2)] : 40;
      return { left: right - 40, right, width: 40, top: 0, bottom: 20, height: 20 };
    };
  }
  const tip = row.querySelector(".tip");
  if (tip) {
    styles.set(tip, { position: "fixed", paddingRight: "0" });
    tip.getBoundingClientRect = () => ({ left: 0, right: 5000, width: 5000, top: 0, bottom: 20, height: 20 });
  }
  // The first measure ran during mount, before the stubs; measure again.
  row.appendChild(document.createElement("i"));
  await view.act(async () => { await frame(); await frame(); });
  return { view, row };
}

async function resize(view, row, next) {
  width = next;
  resizeObservers.trigger(row);
  await view.act(async () => { await frame(); });
}

beforeEach(() => {
  needs = [300, 250, 200];
  width = 400;
  measuringSeen = false;
});

describe("useCompactEnvironmentRow", () => {
  test("a roomy row stays full width, measured with transitions off", async () => {
    const { view, row } = await mount();
    assert.equal(row.dataset.squeeze, undefined);
    assert.equal(row.dataset.compact, undefined);
    assert.equal(measuringSeen, true, "the levels are laid out under data-measuring");
    assert.equal(row.dataset.measuring, undefined, "data-measuring is removed after measuring");
    await view.unmount();
  });

  test("narrowing squeezes one level at a time, down to icon-only compact", async () => {
    const { view, row } = await mount();
    await resize(view, row, 260);
    assert.equal(row.dataset.squeeze, "1");
    assert.equal(row.dataset.compact, undefined);
    await resize(view, row, 210);
    assert.equal(row.dataset.squeeze, "2");
    await resize(view, row, 150);
    assert.equal(row.dataset.squeeze, "3");
    assert.equal(row.dataset.compact, "true");
    await view.unmount();
  });

  test("a squeezed row expands again, with hysteresis at the boundary", async () => {
    const { view, row } = await mount();
    await resize(view, row, 150);
    assert.equal(row.dataset.compact, "true");
    // 255 fits level 1 (250) but not with 12px of slack: it stops at level 2.
    await resize(view, row, 255);
    assert.equal(row.dataset.squeeze, "2");
    assert.equal(row.dataset.compact, undefined);
    await resize(view, row, 400);
    assert.equal(row.dataset.squeeze, undefined, "the need is the last chip's edge, so a wide row goes back to full");
    await view.unmount();
  });

  test("a fixed-position hover tip inside the row does not count as content", async () => {
    const { view, row } = await mount({ tip: true });
    assert.equal(row.dataset.squeeze, undefined);
    assert.equal(row.dataset.compact, undefined);
    await view.unmount();
  });

  test("changed content is measured again", async () => {
    const { view, row } = await mount();
    needs = [600, 550, 500];
    row.appendChild(document.createElement("i"));
    await view.act(async () => { await frame(); await frame(); });
    assert.equal(row.dataset.compact, "true", "the longer content no longer fits at any label level");
    await view.unmount();
  });

  test("a resize picks the level in the observer, with no frame, and writes only what changed", async () => {
    const { view, row } = await mount();
    // Let the content's measurement land first.
    await view.act(async () => { for (let i = 0; i < 4; i++) await frame(); });
    const writes = [];
    const observer = new window.MutationObserver((records) => { for (const r of records) writes.push(r.attributeName); });
    observer.observe(row, { attributes: true, attributeFilter: ["data-squeeze", "data-compact"] });
    const raf = globalThis.requestAnimationFrame;
    let frames = 0;
    globalThis.requestAnimationFrame = window.requestAnimationFrame = (fn) => { frames += 1; return raf(fn); };
    try {
      for (const next of [280, 270, 260, 150, 140, 130]) {
        width = next;
        resizeObservers.trigger(row);
      }
      assert.equal(row.dataset.squeeze, "3", "decided in the observer");
      await view.act(async () => { await frame(); });
      assert.equal(frames, 0, "no frame was asked for");
      // 1 at 280px, 3 (and compact) at 150px, nothing at the widths between.
      assert.deepEqual(writes, ["data-squeeze", "data-squeeze", "data-compact"], "one write per change of level");
    } finally {
      globalThis.requestAnimationFrame = window.requestAnimationFrame = raf;
      observer.disconnect();
      await view.unmount();
    }
  });

  test("unmounting clears the row's squeeze attributes", async () => {
    const { view, row } = await mount();
    await resize(view, row, 150);
    await view.unmount();
    assert.equal(row.dataset.squeeze, undefined);
    assert.equal(row.dataset.compact, undefined);
    assert.equal(row.dataset.measuring, undefined);
  });
});
