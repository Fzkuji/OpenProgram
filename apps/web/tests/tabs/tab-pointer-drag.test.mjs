/**
 * The strip's pointer drag engine (tab-actions/use-tab-pointer-drag.ts) on
 * the real CenterTabStrip: synchronous transfer preparation on press, the
 * start threshold, clamped follow-the-pointer, Chrome-style live reorder by
 * overlap, release outcomes (reorder, cancel, another window, new window)
 * and the cancel paths. Builds on the harness in tab-pointer-release.test.mjs,
 * which covers the floating preview and Resources drops.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render, setRect } from "../_support/dom.mjs";

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
window.Element.prototype.getAnimations = () => [];
const captured = [];
window.Element.prototype.setPointerCapture = function (pointerId) { captured.push([this, pointerId]); };
window.Element.prototype.releasePointerCapture = function () {};
globalThis.getComputedStyle = window.getComputedStyle = () => ({ paddingLeft: "0", paddingRight: "0", borderRadius: "", color: "", font: "" });
globalThis.CSS = { escape: (value) => String(value).replaceAll(":", "\\:") };

const { createElement: h } = await import("react");
const { CenterTabStrip } = await import("../../components/center-tabs/strip/center-tab-strip.tsx");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");
const { dragCoordinator, DRAG_START_THRESHOLD_PX, DETACH_HYSTERESIS_PX } = await import("../../lib/tabs/transfer/tab-drag-coordinator.ts");
const { STRIP_GAP } = await import("../../components/center-tabs/strip/tab-strip-geometry.ts");

const web = (id) => ({ id: `w:${id}`, kind: "web", title: id.toUpperCase(), url: `https://${id}.test/` });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const TAB = 100;
const SLOT = TAB + STRIP_GAP;

let view;
let calls;
let bridge;

function desktop(overrides = {}) {
  calls = [];
  bridge = {
    windowId: "main",
    tabTransfer: {
      prepare: (payload) => { calls.push(["prepare", payload.tabs.map((tab) => tab.id)]); return "token"; },
      cancel: async (token) => { calls.push(["cancel", token]); return true; },
      windowAtCursor: async () => { calls.push(["windowAtCursor"]); return null; },
      deliver: async (token, windowId) => { calls.push(["deliver", token, windowId]); return true; },
      detach: async (token) => { calls.push(["detach", token]); return "window-2"; },
      ...overrides,
    },
  };
  window.openprogramDesktop = bridge;
}

async function mount(tabs = [web("a"), web("b"), web("c"), web("d")], activeId = tabs[0].id) {
  useCenterTabs.setState({ tabs, groups: [], activeId });
  view = await render(h(CenterTabStrip));
  const strip = view.container.querySelector("[data-center-tab-strip]");
  setRect(strip, { left: 0, top: 0, width: 1000, height: 40 });
  const flow = strip.querySelector('[role="tablist"]');
  setRect(flow, { left: 0, top: 4, width: 900, height: 32 });
  flow.getClientRects = () => [{}];
  Object.defineProperty(flow, "clientWidth", { value: 600, configurable: true });
  [...flow.children].forEach((entry, index) => setRect(entry, { left: index * SLOT, top: 4, width: TAB, height: 32 }));
}

const strip = () => view.container.querySelector("[data-center-tab-strip]");
const entry = (id) => strip().querySelector(`[role="tab"][data-tab-id="${id}"]`).parentElement;
const status = () => strip().querySelector('[role="status"]').textContent;
const tabIds = () => useCenterTabs.getState().tabs.map((tab) => tab.id);

function pointer(type, x, y, extra = {}) {
  const event = new window.Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, { button: 0, buttons: type === "pointerup" ? 0 : 1, pointerId: 7, clientX: x, clientY: y, screenX: x, screenY: y, ...extra });
  return event;
}
async function press(id, x = 50, y = 20) {
  await view.act(async () => { entry(id).dispatchEvent(pointer("pointerdown", x, y)); });
}
async function move(x, y) {
  await view.act(async () => { window.dispatchEvent(pointer("pointermove", x, y)); });
  await view.act(flush);
}
async function release(x, y) {
  await view.act(async () => { window.dispatchEvent(pointer("pointerup", x, y)); });
  await view.act(flush);
}

beforeEach(() => {
  captured.length = 0;
  delete window.openprogramDesktop;
  calls = [];
});

afterEach(async () => {
  await view?.act(async () => window.dispatchEvent(new window.Event("pointercancel")));
  await view?.unmount();
  view = undefined;
  dragCoordinator.clear();
  delete window.openprogramDesktop;
});

describe("press", () => {
  test("prepares the main-process transfer synchronously and keeps the token on the coordinator record", async () => {
    desktop();
    await mount();
    await press("w:b");
    assert.deepEqual(calls, [["prepare", ["w:b"]]], "prepared during pointerdown itself, before any await");
    const prepared = dragCoordinator.current();
    assert.equal(prepared.transferToken, "token");
    assert.equal(prepared.started, false);
    assert.equal(entry("w:b").getAttribute("data-pointer-pressed"), "true");
  });

  test("a release before the threshold is a click: the token is released and the tab activates", async () => {
    desktop();
    await mount();
    await press("w:b");
    await move(50 + DRAG_START_THRESHOLD_PX - 2, 20);
    assert.equal(entry("w:b").hasAttribute("data-pointer-drag"), false, "movement under the threshold is not a drag");
    await release(52, 20);
    await view.act(async () => entry("w:b").click());
    assert.deepEqual(calls, [["prepare", ["w:b"]], ["cancel", "token"]]);
    assert.equal(dragCoordinator.current(), null);
    assert.equal(useCenterTabs.getState().activeId, "w:b");
  });

  test("in a plain browser a press prepares a coordinator record with no token", async () => {
    await mount();
    await press("w:b");
    assert.equal(dragCoordinator.current().transferToken, undefined);
    assert.deepEqual(dragCoordinator.current().subject, { kind: "tab", tabIds: ["w:b"] });
  });
});

describe("dragging inside the strip", () => {
  test("crossing the threshold starts the drag, captures the pointer and swallows the follow-up click", async () => {
    await mount();
    await press("w:b", 150, 20);
    await move(150 + DRAG_START_THRESHOLD_PX, 20);
    assert.equal(entry("w:b").getAttribute("data-pointer-drag"), "true");
    assert.equal(dragCoordinator.current().started, true);
    assert.ok(captured.some(([element, id]) => element === entry("w:b") && id === 7), "the dragged tab holds pointer capture");
    assert.equal(status(), "Dragging tab");
    await release(150 + DRAG_START_THRESHOLD_PX, 20);
    await view.act(async () => entry("w:b").click());
    assert.equal(useCenterTabs.getState().activeId, "w:a", "a completed drag never activates its tab");
  });

  test("the tab follows the pointer horizontally, clamped to the strip's visible span", async () => {
    await mount();
    await press("w:b", 150, 20);
    await move(190, 26);
    assert.equal(entry("w:b").style.transform, "translate(40px, 0px)", "inside the band the tab stays on the strip's row");
    await move(-400, 20);
    assert.equal(entry("w:b").style.transform, `translate(${-SLOT}px, 0px)`, "never left of the visible span");
    await move(2000, 20);
    // The flow's visible box is 600px wide; the tab's right edge stops there.
    assert.equal(entry("w:b").style.transform, `translate(${600 - (SLOT + TAB)}px, 0px)`);
  });

  test("a neighbour yields once half of it is covered, and its bystander slides one slot aside", async () => {
    await mount();
    await press("w:a", 50, 20);
    await move(50 + 0.4 * SLOT, 20);
    assert.equal(entry("w:b").style.transform, "", "under half covered: nothing moves");
    await move(50 + 0.6 * SLOT, 20);
    assert.equal(entry("w:b").style.transform, `translateX(-${SLOT}px)`);
    assert.equal(entry("w:c").style.transform, "");
  });

  test("a fast flick across several tabs shifts every crossed tab by exactly one slot", async () => {
    await mount();
    await press("w:a", 50, 20);
    await move(50 + 2.3 * SLOT, 20);
    assert.deepEqual(["w:b", "w:c", "w:d"].map((id) => entry(id).style.transform),
      [`translateX(-${SLOT}px)`, `translateX(-${SLOT}px)`, ""]);
  });

  test("between two slots the last intent is held, so bystanders do not flicker back", async () => {
    await mount();
    await press("w:a", 50, 20);
    await move(50 + 2.3 * SLOT, 20);
    assert.equal(entry("w:c").style.transform, `translateX(-${SLOT}px)`);
    await move(50 + 1.5 * SLOT, 20); // in the gap: neither b nor c is half covered
    assert.equal(entry("w:c").style.transform, `translateX(-${SLOT}px)`);
    await move(50 + 0.1 * SLOT, 20); // back over its own slot: no intent
    assert.equal(entry("w:b").style.transform, "");
    assert.equal(entry("w:c").style.transform, "");
  });

  test("the dragged tab keeps its live offset across the re-renders an intent change causes", async () => {
    await mount();
    await press("w:a", 50, 20);
    await move(50 + 0.6 * SLOT, 20);
    assert.equal(entry("w:a").style.transform, `translate(${0.6 * SLOT}px, 0px)`);
  });

  test("releasing on an intent reorders the store, releases the unused token and announces it", async () => {
    desktop();
    await mount();
    await press("w:a", 50, 20);
    await move(50 + 1.6 * SLOT, 20);
    await release(50 + 1.6 * SLOT, 20);
    assert.deepEqual(tabIds(), ["w:b", "w:c", "w:a", "w:d"]);
    assert.ok(calls.some(([name, token]) => name === "cancel" && token === "token"), "a same-window drop releases its transfer token");
    assert.ok(!calls.some(([name]) => name === "detach" || name === "deliver"));
    assert.equal(status(), "Tab reordered");
    assert.equal(entry("w:a").hasAttribute("data-pointer-drag"), false);
    assert.equal(entry("w:a").style.transform, "");
  });

  test("releasing with no intent slides the tab home and changes nothing", async () => {
    await mount();
    await press("w:b", 150, 20);
    await move(160, 20);
    await release(160, 20);
    assert.deepEqual(tabIds(), ["w:a", "w:b", "w:c", "w:d"]);
    assert.equal(entry("w:b").style.transform, "");
    assert.equal(status(), "Tab move cancelled");
  });

  test("a whole split group drags as one entry", async () => {
    const group = { id: "g", memberIds: ["w:a", "w:b"], visibleIds: ["w:a", "w:b"], focusedId: "w:a" };
    useCenterTabs.setState({ tabs: [web("a"), web("b"), web("c")], groups: [group], activeId: "w:c" });
    view = await render(h(CenterTabStrip));
    const compound = view.container.querySelector('[data-member-count="2"]');
    await view.act(async () => { compound.dispatchEvent(pointer("pointerdown", 50, 20)); });
    assert.deepEqual(dragCoordinator.current().subject.tabIds, ["w:a", "w:b"]);
    assert.equal(dragCoordinator.current().subject.kind, "group");
  });
});

describe("leaving the strip", () => {
  test("detach intent needs a full tab height past the edge and clears as soon as the cursor is home", async () => {
    desktop();
    await mount();
    await press("w:b", 150, 20);
    await move(150, 40 + DETACH_HYSTERESIS_PX - 5);
    assert.equal(entry("w:b").hasAttribute("data-detach-intent"), false, "just past the edge is not yet a tear-off");
    await move(150, 40 + DETACH_HYSTERESIS_PX + 5);
    assert.equal(entry("w:b").hasAttribute("data-detach-intent"), true);
    await move(150, 30);
    assert.equal(entry("w:b").hasAttribute("data-detach-intent"), false, "back inside the strip rectangle cancels at once");
    await move(150, -DETACH_HYSTERESIS_PX - 5);
    assert.equal(entry("w:b").hasAttribute("data-detach-intent"), true, "leaving through the top edge detaches too");
  });

  test("outside the band the tab follows the pointer on both axes", async () => {
    desktop();
    await mount();
    await press("w:b", 150, 20);
    await move(170, 160);
    const preview = document.querySelector("[data-tab-drag-preview]");
    assert.equal(preview.style.transform, "translate(20px, 140px)");
    assert.equal(entry("w:b").style.transform, "translate(20px, 140px)");
  });

  test("without a transfer token (plain browser) leaving the strip never becomes a tear-off", async () => {
    await mount();
    await press("w:b", 150, 20);
    await move(150, 200);
    assert.equal(entry("w:b").hasAttribute("data-detach-intent"), false);
  });

  test("the window under the cursor is polled for the whole drag, not only while detaching", async () => {
    desktop();
    await mount();
    await press("w:b", 150, 20);
    await move(170, 20);
    assert.ok(calls.some(([name]) => name === "windowAtCursor"));
  });

  test("a release outside with no window under the cursor detaches the token into a new window", async () => {
    desktop();
    await mount();
    await press("w:b", 150, 20);
    await move(150, 200);
    calls.length = 0;
    await release(150, 200);
    await view.act(flush);
    assert.deepEqual(calls, [["windowAtCursor"], ["detach", "token"]], "the live hit test runs before falling back to detach");
    assert.equal(status(), "Tab moved to new window");
    assert.equal(dragCoordinator.current(), null, "main owns the token now");
  });

  test("a release over another window delivers the tab there instead", async () => {
    desktop({ windowAtCursor: async () => { calls.push(["windowAtCursor"]); return "window-9"; } });
    await mount();
    await press("w:b", 150, 20);
    await move(150, 200);
    calls.length = 0;
    await release(150, 200);
    await view.act(flush);
    assert.deepEqual(calls, [["windowAtCursor"], ["deliver", "token", "window-9"]]);
    assert.equal(status(), "Tab moved");
  });

  test("a refused detach announces the cancel", async () => {
    desktop({ detach: async () => null });
    await mount();
    await press("w:b", 150, 20);
    await move(150, 200);
    await release(150, 200);
    await view.act(flush);
    assert.equal(status(), "Tab move cancelled");
  });

  test("a lone tab moves its whole window and never tears into a new one", async () => {
    desktop();
    const moves = [];
    bridge.moveWindowBy = (dx, dy) => moves.push([dx, dy]);
    await mount([web("a")]);
    await press("w:a", 50, 20);
    await move(70, 200);
    await move(80, 260);
    assert.deepEqual(moves, [[20, 180], [10, 60]]);
    assert.equal(entry("w:a").hasAttribute("data-detach-intent"), false);
  });
});

describe("cancelling", () => {
  for (const [name, cancel] of [
    ["pointercancel", () => window.dispatchEvent(new window.Event("pointercancel"))],
    ["window blur", () => window.dispatchEvent(new window.Event("blur"))],
    ["Escape", () => {
      const event = new window.Event("keydown", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "key", { value: "Escape" });
      document.dispatchEvent(event);
    }],
  ]) {
    test(`${name} slides the tab home, releases the token and announces the cancel`, async () => {
      desktop();
      await mount();
      await press("w:b", 150, 20);
      await move(150 + 0.6 * SLOT, 20);
      await view.act(async () => cancel());
      await view.act(flush);
      assert.equal(entry("w:b").hasAttribute("data-pointer-drag"), false);
      assert.equal(entry("w:b").style.transform, "");
      assert.equal(entry("w:c").style.transform, "", "bystanders return too");
      assert.ok(calls.some(([call, token]) => call === "cancel" && token === "token"));
      assert.equal(dragCoordinator.current(), null);
      assert.equal(status(), "Tab move cancelled");
      assert.deepEqual(tabIds(), ["w:a", "w:b", "w:c", "w:d"]);
    });
  }
});
