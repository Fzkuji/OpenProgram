// The composer caches its input wrapper's height while idle, as the start
// of the function form's height transition (use-fn-form-wrapper.ts). The
// height comes from the ResizeObserver's entry: reading offsetHeight in the
// callback laid the page out again whenever an earlier callback in the same
// frame had written a style, which the tuck band's ring does in every frame
// a moving pane resizes the input box.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers } from "../../_support/dom.mjs";

installLoader();
const window = installDom();
const { createElement: h, useRef } = await import("react");
const { useFnFormWrapper } = await import("../../../components/chat/composer/modes/fn-form/use-fn-form-wrapper.ts");

let heightReads = 0;
Object.defineProperty(window.HTMLElement.prototype, "offsetHeight", {
  configurable: true,
  get() { if (this.hasAttribute("data-wrapper")) heightReads += 1; return 0; },
});

// The open form's own sizing reads computed styles; none matter here.
globalThis.getComputedStyle = window.getComputedStyle = () => ({ paddingTop: "0", paddingBottom: "0", gap: "0", fontSize: "14", lineHeight: "21", position: "static", getPropertyValue: () => "" });

const noop = () => {};
function Composer({ fn }) {
  const wrapperRef = useRef(null);
  useFnFormWrapper({ fnFormFunction: fn, fnFormClosing: false, onCloseComplete: noop, wrapperRef });
  return h("div", { ref: wrapperRef, "data-wrapper": "" });
}

test("the idle wrapper's height comes from the observer's entry, with no layout read", async () => {
  const view = await render(h(Composer, { fn: null }));
  const wrapper = view.container.querySelector("[data-wrapper]");
  const observer = [...resizeObservers.instances].find((instance) => instance.targets.has(wrapper));
  assert.ok(observer, "the wrapper is observed");
  heightReads = 0;
  // The input grew a line while a pane moved.
  observer.callback([{ target: wrapper, borderBoxSize: [{ inlineSize: 600, blockSize: 131.6 }], contentRect: { width: 576, height: 107.6 } }], observer);
  assert.equal(heightReads, 0, "no offsetHeight read in the callback");
  // Opening a function form starts its height transition from that height.
  const heights = [];
  const style = wrapper.style;
  Object.defineProperty(wrapper, "style", {
    configurable: true,
    value: new Proxy(style, {
      get: (target, key) => target[key],
      set: (target, key, value) => { if (key === "height") heights.push(value); target[key] = value; return true; },
    }),
  });
  await view.rerender(h(Composer, { fn: { name: "summarize" } }));
  assert.equal(heights[0], "132px");
  await view.unmount();
});
