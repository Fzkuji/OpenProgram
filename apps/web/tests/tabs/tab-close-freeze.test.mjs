/**
 * Chrome's close-with-the-mouse width freeze: clicking a tab's × pins the
 * survivors to their current widths so the next × lands under the cursor;
 * leaving the strip, a resize, a new tab or a drag press releases the pin.
 * The pin itself (freezeStripWidths / releaseStripWidths) and the strip that
 * drives it (CenterTabStrip with its lifecycle hook) both run for real.
 */
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/components/animated-icons": [
      "import { forwardRef, createElement } from 'react';",
      "const Icon = forwardRef(() => createElement('svg'));",
      "export const ChromeIcon = Icon, FeatherIcon = Icon, MessageCircleIcon = Icon;",
    ].join("\n"),
    "@/components/files/file-type-icon": "export const FileTypeIcon = () => null;",
    "@/components/applications/application-glyph": "export const ApplicationTabGlyph = () => null;",
  },
  replace: {
    "/components/center-tabs/browser/toolbar/page-navigation": "export const PageNavigation = () => null;",
    "/components/center-tabs/strip/main-menu.tsx": "export const MainMenu = () => null;",
  },
});
installDom({ pathname: "/chat" });
window.Element.prototype.animate = () => ({ finished: Promise.resolve(), cancel() {} });
globalThis.CSS = { escape: (value) => String(value).replaceAll(":", "\\:") };
// linkedom has no MouseEvent; the lifecycle tells a physical click from a
// keyboard activation of the × by `instanceof MouseEvent` and `detail`.
class MouseEvent extends window.Event {
  constructor(type, init = {}) {
    super(type, init);
    this.detail = init.detail ?? 0;
    this.button = init.button ?? 0;
    this.relatedTarget = init.relatedTarget ?? null;
  }
}
globalThis.MouseEvent = window.MouseEvent = MouseEvent;

const { createElement: h } = await import("react");
const geometry = await import("../../components/center-tabs/strip/tab-strip-geometry.ts");
const { CenterTabStrip } = await import("../../components/center-tabs/strip/center-tab-strip.tsx");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");

const web = (id) => ({ id: `w:${id}`, kind: "web", title: id.toUpperCase(), url: `https://${id}.test/` });
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

describe("freezeStripWidths and releaseStripWidths", () => {
  function row(widths, closingIndex = -1) {
    const flow = document.createElement("div");
    widths.forEach((width, index) => {
      const child = document.createElement("div");
      if (index === closingIndex) child.dataset.tabClosing = "true";
      setRect(child, { width, height: 32 });
      flow.append(child);
    });
    return flow;
  }
  const pins = (child) => [child.style.width, child.style.flexBasis, child.style.maxWidth, child.style.flexShrink];

  test("pins width, flex-basis, max-width and flex-shrink of every survivor to its measured width", () => {
    const flow = row([216, 180, 0]);
    geometry.freezeStripWidths(flow);
    const [first, second, empty] = flow.children;
    assert.deepEqual(pins(first), ["216px", "216px", "216px", "0"]);
    assert.deepEqual(pins(second), ["180px", "180px", "180px", "0"]);
    assert.equal(first.dataset.widthFrozen, "true");
    assert.equal(empty.dataset.widthFrozen, undefined, "a zero-width child is left alone");
  });

  test("measures every child before pinning any, so the row never records a staircase", () => {
    const flow = row([200, 200, 200]);
    // Pinning a child with flex-shrink:0 would change its siblings' widths.
    const original = window.HTMLElement.prototype.getBoundingClientRect;
    window.HTMLElement.prototype.getBoundingClientRect = function () {
      const pinned = [...flow.children].filter((child) => child.style.flexShrink === "0").length;
      return { ...original.call(this), width: 200 - pinned * 10 };
    };
    try {
      geometry.freezeStripWidths(flow);
    } finally {
      window.HTMLElement.prototype.getBoundingClientRect = original;
    }
    assert.deepEqual([...flow.children].map((child) => child.style.width), ["200px", "200px", "200px"]);
  });

  test("a closing tab keeps its natural width so its exit animation can shrink it", () => {
    const flow = row([200, 200], 0);
    geometry.freezeStripWidths(flow);
    assert.deepEqual(pins(flow.children[0]), ["", "", "", ""]);
    assert.equal(flow.children[1].style.width, "200px");
  });

  test("release clears the pins and keeps a 'released' marker until the transition ends", () => {
    const flow = row([200, 150]);
    geometry.freezeStripWidths(flow);
    geometry.releaseStripWidths(flow);
    const [first] = flow.children;
    assert.deepEqual(pins(first), ["", "", "", ""]);
    assert.equal(first.dataset.widthFrozen, "released", "the transition rule still matches while they grow back");
    first.dispatchEvent(new window.Event("transitionend"));
    assert.equal(first.dataset.widthFrozen, undefined);
  });

  test("null flows are ignored", () => {
    geometry.freezeStripWidths(null);
    geometry.releaseStripWidths(null);
  });
});

describe("the strip freezes on a mouse close and releases on anything that relayouts", () => {
  let view;
  afterEach(async () => {
    await view?.unmount();
    view = undefined;
  });

  async function mount(tabs = [web("a"), web("b"), web("c")]) {
    useCenterTabs.setState({ tabs, groups: [], activeId: tabs[0].id });
    view = await render(h(CenterTabStrip));
    for (const entry of flow().children) setRect(entry, { width: 200, height: 32 });
  }
  const strip = () => view.container.querySelector("[data-center-tab-strip]");
  const flow = () => strip().querySelector('[role="tablist"]');
  const entry = (id) => flow().querySelector(`[data-tab-id="${id}"]`).parentElement;
  const frozen = () => [...flow().children].map((child) => child.dataset.widthFrozen ?? "-");
  const closeButton = (id) => entry(id).querySelector("button");
  async function dispatch(target, event) {
    await view.act(async () => { target.dispatchEvent(event); });
    await view.act(() => wait(0));
  }
  const mouseClose = (id) => dispatch(closeButton(id), new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }));
  const keyboardClose = (id) => dispatch(closeButton(id), new MouseEvent("click", { bubbles: true, cancelable: true, detail: 0 }));

  test("a mouse click on × pins the survivors but not the closing tab", async () => {
    await mount();
    await mouseClose("w:b");
    assert.equal(entry("w:b").dataset.tabClosing, "true");
    assert.deepEqual(frozen(), ["true", "-", "true"]);
    assert.equal(entry("w:a").style.width, "200px");
  });

  test("a keyboard activation of × (detail 0) closes without pinning and releases an earlier pin", async () => {
    await mount();
    await mouseClose("w:c");
    assert.deepEqual(frozen().slice(0, 2), ["true", "true"]);
    await keyboardClose("w:b");
    assert.equal(entry("w:a").dataset.widthFrozen, "released");
    assert.equal(entry("w:a").style.width, "");
  });

  test("leaving the strip releases after a grace period; coming back before it keeps the pin", async () => {
    await mount();
    await mouseClose("w:b");
    // React derives mouseleave/mouseenter from mouseout/mouseover and their relatedTarget.
    const leave = () => dispatch(strip(), new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body }));
    const enter = () => dispatch(strip(), new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body }));
    await leave();
    await enter();
    await view.act(() => wait(450));
    assert.equal(entry("w:a").dataset.widthFrozen, "true", "re-entering cancelled the release");
    await leave();
    await view.act(() => wait(300));
    assert.equal(entry("w:a").dataset.widthFrozen, "true", "a graze shorter than the grace period keeps the pin");
    await view.act(() => wait(150));
    assert.equal(entry("w:a").dataset.widthFrozen, "released");
  });

  test("a window resize releases at once", async () => {
    await mount();
    await mouseClose("w:b");
    await view.act(async () => window.dispatchEvent(new window.Event("resize")));
    assert.equal(entry("w:a").dataset.widthFrozen, "released");
  });

  test("the strip box resizing without a window resize (sidebar toggle, split drag) releases", async () => {
    await mount();
    await mouseClose("w:b");
    await view.act(async () => resizeObservers.trigger(strip()));
    assert.equal(entry("w:a").dataset.widthFrozen, "released");
  });

  test("opening a new tab releases before the new tab enters", async () => {
    await mount();
    await mouseClose("w:b");
    const newTab = strip().querySelector('button[aria-label="New tab"]');
    await dispatch(newTab, new MouseEvent("click", { bubbles: true, detail: 1 }));
    assert.equal(entry("w:a").dataset.widthFrozen, "released");
    assert.equal(useCenterTabs.getState().tabs.at(-1).kind, "ntp");
  });

  test("pressing a tab releases the pin before the drag engine snapshots slot geometry", async () => {
    await mount();
    await mouseClose("w:b");
    const press = new window.Event("pointerdown", { bubbles: true, cancelable: true });
    Object.assign(press, { button: 0, pointerId: 3, clientX: 10, clientY: 10 });
    await dispatch(entry("w:a"), press);
    assert.equal(entry("w:a").dataset.widthFrozen, "released");
    await dispatch(window, new window.Event("pointercancel"));
  });

  test("closing one pane of a split that stays removes it at once, with no strip exit", async () => {
    const group = { id: "g", memberIds: ["w:a", "w:b", "w:c"], visibleIds: ["w:a", "w:b", "w:c"], focusedId: "w:a" };
    useCenterTabs.setState({ tabs: [web("a"), web("b"), web("c"), web("d")], groups: [group], activeId: "w:a" });
    view = await render(h(CenterTabStrip));
    await view.act(async () => window.dispatchEvent(new window.CustomEvent("op-close-center-tabs", { detail: { tabIds: ["w:b"] } })));
    await view.act(() => wait(0));
    assert.deepEqual(useCenterTabs.getState().tabs.map((tab) => tab.id), ["w:a", "w:c", "w:d"]);
    assert.equal(strip().querySelector("[data-tab-closing]"), null);
  });

  test("closing a whole split plays one exit on the split's tab and removes every member when it ends", async () => {
    const group = { id: "g", memberIds: ["w:a", "w:b"], visibleIds: ["w:a", "w:b"], focusedId: "w:a" };
    useCenterTabs.setState({ tabs: [web("a"), web("b"), web("c")], groups: [group], activeId: "w:a" });
    view = await render(h(CenterTabStrip));
    const compound = strip().querySelector('[data-member-count="2"]');
    await dispatch(compound.querySelector("button"), new MouseEvent("click", { bubbles: true, detail: 0 }));
    assert.equal(compound.dataset.tabClosing, "true");
    assert.match(compound.className, /tabExit/);
    assert.equal(useCenterTabs.getState().tabs.length, 3, "nothing leaves the store before the exit animation ends");
    await dispatch(compound, new window.Event("animationend", { bubbles: true }));
    assert.deepEqual(useCenterTabs.getState().tabs.map((tab) => tab.id), ["w:c"]);
  });

  test("Cmd+W from the desktop menu closes the active entry through the same transaction", async () => {
    const group = { id: "g", memberIds: ["w:a", "w:b"], visibleIds: ["w:a", "w:b"], focusedId: "w:a" };
    useCenterTabs.setState({ tabs: [web("a"), web("b"), web("c")], groups: [group], activeId: "w:b" });
    view = await render(h(CenterTabStrip));
    await view.act(async () => window.dispatchEvent(new window.Event("op-desktop-close-tab")));
    await view.act(() => wait(0));
    const compound = strip().querySelector('[data-member-count="2"]');
    assert.equal(compound.dataset.tabClosing, "true", "both members of the active split close together");
  });
});
