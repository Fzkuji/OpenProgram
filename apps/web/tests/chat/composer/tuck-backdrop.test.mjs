// With the composer's rows tucked behind the input box, the chat mask
// above the box (composer.module.css .inputArea::before/::after) starts
// lower: 12px above whatever follows the tucked row, not at the row's
// top, so the strip the row leaves empty shows the transcript. The
// published composer height, which places Jump to latest and reserves the
// transcript's bottom, leaves that strip out too. Untucked, the mask is
// where it always was.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader();
const window = installDom();
const { createElement: h, useRef } = await import("react");
const { useTuckBackdrop, tuckedShift } = await import("../../../components/chat/composer/controls/use-tuck-backdrop.ts");
const { visibleComposerHeight, COMPOSER_TUCK_SHIFT_EVENT } = await import("../../../lib/chat/history/chat-scroll.ts");

const root = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (path) => readFileSync(join(root, path), "utf8");

// Layout from data attributes: the decision column, the row above (top 12,
// 40 tall), the box (top 52), the controls row; the area is 140 tall.
for (const [prop, attr] of [["offsetTop", "data-top"], ["offsetHeight", "data-height"]]) {
  Object.defineProperty(window.HTMLElement.prototype, prop, {
    configurable: true,
    get() { return Number(this.getAttribute(attr) ?? 0); },
  });
}

function Area({ tucked, decisions = 0 }) {
  const area = useRef(null);
  useTuckBackdrop(area, tucked);
  return h("div", { ref: area, "data-area": "", "data-height": 140 },
    h("div", { "data-height": decisions }),
    h("div", { "data-tuck-side": "above", "data-top": 12 + decisions, "data-height": 40 }),
    h("div", { "data-box": "", "data-top": 52 + decisions, "data-height": 52 }),
    h("div", { "data-tuck-side": "below", "data-top": 104 + decisions, "data-height": 36 }));
}

test("tucked, the mask starts 12px above the box and the covered height leaves the strip out", async () => {
  const view = await render(h(Area, { tucked: true }));
  const area = view.container.querySelector("[data-area]");
  assert.equal(area.style.getPropertyValue("--composer-tuck-shift"), "28px");
  assert.equal(visibleComposerHeight(area), 112);
  await view.unmount();
});

test("untucked, the mask is where it was and the whole composer counts", async () => {
  const view = await render(h(Area, { tucked: false }));
  const area = view.container.querySelector("[data-area]");
  assert.equal(area.style.getPropertyValue("--composer-tuck-shift"), "0px");
  assert.equal(visibleComposerHeight(area), 140);
  await view.unmount();
});

test("panels above the tucked row keep the mask at the top", async () => {
  const view = await render(h(Area, { tucked: true, decisions: 60 }));
  const area = view.container.querySelector("[data-area]");
  assert.equal(tuckedShift(area), 0);
  await view.unmount();
});

test("each shift tells the height publishers", async () => {
  const view = await render(h(Area, { tucked: false }));
  const area = view.container.querySelector("[data-area]");
  let told = 0;
  area.addEventListener(COMPOSER_TUCK_SHIFT_EVENT, () => { told += 1; });
  await view.rerender(h(Area, { tucked: true }));
  await view.act(async () => { await new Promise((resolve) => setTimeout(resolve, 600)); });
  assert.ok(told > 0);
  assert.equal(area.style.getPropertyValue("--composer-tuck-shift"), "28px");
  await view.unmount();
});

test("the mask, the height publishers and the rows share the shift and the spring", () => {
  const css = read("components/chat/composer/composer.module.css");
  assert.match(css, /inset: calc\(12px \+ var\(--composer-tuck-shift, 0px\)\) 0 0;/);
  assert.match(css, /top: calc\(-16px \+ var\(--composer-tuck-shift, 0px\)\);/);
  assert.match(read("components/chat/composer/controls/tuck-row.tsx"), /\{ \.\.\.TUCK_SLIDE, opacity: \{ duration: 0\.18 \} \}/);
  assert.match(read("components/chat/composer/input/use-composer-input-effects.ts"), /visibleComposerHeight\(element\)/);
  assert.match(read("components/chat/pane/peer-session-pane.tsx"), /visibleComposerHeight\(target\)/);
  assert.match(read("components/chat/composer/index.tsx"), /useTuckBackdrop\(inputAreaRef, tuck\.tucked\)/);
});
