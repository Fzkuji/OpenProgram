/**
 * A menu or popover whose trigger leaves the layout hides instead of
 * jumping to the window's top-left corner.
 *
 * Choosing "Manage providers" in the composer's model menu routes to
 * Settings, which hides the chat column (display: none) while the menu is
 * still playing its close animation. Without hiding, Floating UI places the
 * fading panel against the trigger's zero rect at (0, 0), over the left
 * sidebar, for the length of the fade.
 */
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../../_support/dom.mjs";

installLoader();
installDom({ pathname: "/chat" });
window.innerWidth = 1512;
window.innerHeight = 808;
// linkedom computes no styles: every element is a plain static block, the
// root of Floating UI's clipping walk is the viewport.
const STATIC_BLOCK = {
  display: "block", position: "static", overflow: "visible", overflowX: "visible", overflowY: "visible",
  transform: "none", translate: "none", scale: "none", rotate: "none", perspective: "none",
  containerType: "normal", backdropFilter: "none", filter: "none", willChange: "auto", contain: "none",
  zIndex: "auto", animationName: "none", direction: "ltr", width: "0px", height: "0px",
  borderTopWidth: "0px", borderLeftWidth: "0px", getPropertyValue: () => "",
};
globalThis.getComputedStyle = window.getComputedStyle = () => STATIC_BLOCK;
// The popover's focus scope walks its content for tabbables.
globalThis.NodeFilter ??= { SHOW_ELEMENT: 1, FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3 };

const { createElement: h } = await import("react");
const { act } = await import("react");
const {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
} = await import("../../../components/ui/dropdown-menu.tsx");
const { Popover, PopoverTrigger, PopoverContent } = await import("../../../components/ui/popover.tsx");

const settle = async () => {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); });
  }
};

let view;
afterEach(async () => { await view?.unmount(); view = null; });

async function openAgainst(tree) {
  view = await render(tree);
  await settle();
  const trigger = document.querySelector("[data-anchor]");
  const wrapper = () => document.querySelector("[data-radix-popper-content-wrapper]");
  assert.ok(wrapper(), "the panel is open");
  return { trigger, wrapper };
}

async function moveAnchor(trigger, rect) {
  setRect(trigger, rect);
  await act(async () => { resizeObservers.trigger(trigger); });
  await settle();
}

const ON_SCREEN = { left: 780, top: 760, width: 120, height: 28 };
const OUT_OF_LAYOUT = { left: 0, top: 0, width: 0, height: 0 };

for (const [name, tree] of [
  ["dropdown menu", h(DropdownMenu, { open: true, modal: false },
    h(DropdownMenuTrigger, { asChild: true }, h("button", { "data-anchor": "" }, "Model")),
    h(DropdownMenuContent, null, h(DropdownMenuItem, null, "Manage providers")))],
  ["popover", h(Popover, { open: true },
    h(PopoverTrigger, { asChild: true }, h("button", { "data-anchor": "" }, "Environment")),
    h(PopoverContent, null, "Add environments"))],
]) {
  describe(name, () => {
    test("shows while its trigger is laid out and hides once the trigger leaves the layout", async () => {
      const { trigger, wrapper } = await openAgainst(tree);
      await moveAnchor(trigger, ON_SCREEN);
      assert.notEqual(wrapper().style.visibility, "hidden");

      await moveAnchor(trigger, OUT_OF_LAYOUT);
      assert.equal(wrapper().style.visibility, "hidden");
      assert.equal(wrapper().style.pointerEvents, "none");
    });
  });
}
