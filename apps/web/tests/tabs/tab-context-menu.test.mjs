/**
 * The tab strip's context menu and keyboard model, on the real
 * CenterTabStrip: how the menu opens and is dismissed, what its actions do
 * to the center-tab store, and "Move to new window" through the desktop
 * transfer bridge.
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
// linkedom does not track focus: remember the last focused element.
let focused = null;
window.HTMLElement.prototype.focus = function focus() { focused = this; };
Object.defineProperty(document, "activeElement", { get: () => focused, configurable: true });
// Web Animations are not emulated; the tab name/glyph swap only needs the call to exist.
window.Element.prototype.animate = () => ({ finished: Promise.resolve(), cancel() {} });
globalThis.CSS = { escape: (value) => String(value).replaceAll(":", "\\:") };
window.innerWidth = 1200;
window.innerHeight = 800;

const { createElement: h } = await import("react");
const { CenterTabStrip } = await import("../../components/center-tabs/strip/center-tab-strip.tsx");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");
const { dragCoordinator } = await import("../../lib/tabs/transfer/tab-drag-coordinator.ts");

const web = (id) => ({ id: `w:${id}`, kind: "web", title: id.toUpperCase(), url: `https://${id}.test/` });
const tick = () => new Promise((resolve) => setTimeout(resolve, 5));

let view;
async function mount(state) {
  useCenterTabs.setState({ groups: [], ...state });
  const centerBody = document.createElement("div");
  centerBody.className = "center-body";
  document.body.append(centerBody);
  view = await render(h(CenterTabStrip));
  return view;
}

const strip = () => view.container.querySelector("[data-center-tab-strip]");
const menu = () => strip().querySelector('[role="menu"]');
const tabTarget = (id) => strip().querySelector(`[role="tab"][data-tab-id="${id}"]`);
const menuItem = (label) => [...menu().querySelectorAll('[role="menuitem"]')].find((item) => item.textContent.includes(label));
const status = () => strip().querySelector('[role="status"]').textContent;
const tabIds = () => useCenterTabs.getState().tabs.map((tab) => tab.id);

async function fire(target, type, props = {}, EventClass = window.Event) {
  await view.act(async () => {
    const event = new EventClass(type, { bubbles: true, cancelable: true });
    for (const [key, value] of Object.entries(props)) {
      Object.defineProperty(event, key, { value, configurable: true });
    }
    target.dispatchEvent(event);
  });
}
const openMenu = (id) => fire(tabTarget(id), "contextmenu", { clientX: 120, clientY: 30, button: 2 });
const click = async (element) => { await view.act(async () => element.click()); await view.act(tick); };

beforeEach(() => {
  delete window.openprogramDesktop;
});

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  dragCoordinator.clear();
  document.querySelector(".center-body")?.remove();
  delete window.openprogramDesktop;
});

describe("opening and dismissing the menu", () => {
  test("right-click opens the menu at the pointer and focuses its first enabled item", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    assert.ok(menu(), "the menu renders inside the strip");
    assert.equal(menu().style.left, "120px");
    assert.equal(menu().style.top, "30px");
    await view.act(tick);
    assert.equal(document.activeElement?.textContent.includes("Move right"), true,
      "Move left is disabled for the first tab, so focus lands on Move right");
  });

  test("Shift+F10 on a focused tab opens the same menu", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    setRect(tabTarget("w:b"), { left: 200, top: 4, width: 180, height: 32 });
    await fire(tabTarget("w:b"), "keydown", { key: "F10", shiftKey: true });
    assert.ok(menu());
    assert.equal(menu().style.top, "36px", "a keyboard-opened menu anchors under the tab");
  });

  test("a pointerdown outside dismisses it, one inside does not", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    await fire(menuItem("Close tab"), "pointerdown");
    assert.ok(menu(), "pressing inside the menu keeps it open");
    await fire(document.body, "pointerdown");
    assert.ok(!menu());
  });

  test("an outside press on another tab dismisses the menu even though the tab handles pointerdown itself", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    await fire(tabTarget("w:b").parentElement, "pointerdown", { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
    assert.ok(!menu());
  });

  for (const type of ["blur", "scroll", "resize"]) {
    test(`a window ${type} closes the fixed-position menu`, async () => {
      await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
      await openMenu("w:a");
      await view.act(async () => window.dispatchEvent(new window.Event(type)));
      assert.ok(!menu());
    });
  }

  test("Escape pressed inside the menu closes it and announces the cancel", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    await fire(menu().querySelector("button"), "keydown", { key: "Escape" });
    assert.ok(!menu());
    assert.equal(status(), "Tab move cancelled");
    await view.act(tick);
    assert.ok(document.activeElement === tabTarget("w:a"), "focus returns to the tab that opened the menu");
    const live = strip().querySelector('[role="status"]');
    assert.equal(live.getAttribute("aria-live"), "polite", "announcements go through a polite live region");
  });

  test("while the menu is open a tab press is a dismissal, not the start of a drag", async () => {
    let prepared = 0;
    window.openprogramDesktop = {
      windowId: "main",
      tabTransfer: { prepare: () => { prepared += 1; return "token"; }, cancel: async () => true },
    };
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    // The capture-phase dismissal runs first; the tab's own handler must
    // still see the menu as open and not arm a drag.
    await fire(tabTarget("w:b").parentElement, "pointerdown", { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
    assert.equal(prepared, 0);
    assert.equal(dragCoordinator.current(), null);
  });

  test("a right or middle press never prepares a drag", async () => {
    let prepared = 0;
    window.openprogramDesktop = {
      windowId: "main",
      tabTransfer: { prepare: () => { prepared += 1; return "token"; }, cancel: async () => true },
    };
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    for (const button of [1, 2]) {
      await fire(tabTarget("w:b").parentElement, "pointerdown", { button, pointerId: 1, clientX: 10, clientY: 10 });
    }
    assert.equal(prepared, 0);
  });
});

describe("menu actions", () => {
  test("Move right reorders the tab, closes the menu and announces it", async () => {
    await mount({ tabs: [web("a"), web("b"), web("c")], activeId: "w:a" });
    await openMenu("w:a");
    await click(menuItem("Move right"));
    assert.deepEqual(tabIds(), ["w:b", "w:a", "w:c"]);
    assert.ok(!menu());
    assert.equal(status(), "Tab reordered");
    assert.ok(document.activeElement === tabTarget("w:a"), "focus returns to the tab the menu acted on");
  });

  test("moving a member of a split moves the whole group, never just that member", async () => {
    const group = { id: "g", memberIds: ["w:b", "w:c"], visibleIds: ["w:b", "w:c"], focusedId: "w:b" };
    await mount({ tabs: [web("a"), web("b"), web("c"), web("d")], groups: [group], activeId: "w:a" });
    await openMenu("w:b");
    await click(menuItem("Move left"));
    assert.deepEqual(tabIds(), ["w:b", "w:c", "w:a", "w:d"]);
    assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["w:b", "w:c"]);
  });

  test("move items are disabled at the ends of the strip", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:b");
    assert.equal(menuItem("Move right").disabled, true);
    assert.equal(menuItem("Move left").disabled, false);
  });

  test("Close tab on a split member closes the whole split entry", async () => {
    const group = { id: "g", memberIds: ["w:a", "w:b"], visibleIds: ["w:a", "w:b"], focusedId: "w:a" };
    await mount({ tabs: [web("a"), web("b"), web("c")], groups: [group], activeId: "w:c" });
    await openMenu("w:a");
    await click(menuItem("Close tab"));
    assert.ok(!menu());
    const compound = strip().querySelector('[data-member-count="2"]');
    assert.equal(compound.getAttribute("data-tab-closing"), "true", "both members leave through the exit animation");
  });

  test("Close other tabs and Close tabs to the right are disabled when nothing would close", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:b");
    assert.equal(menuItem("Close tabs to the right").disabled, true);
    assert.equal(menuItem("Close other tabs").disabled, false);
  });

  test("Remove from group dissolves the split and is disabled for a lone tab", async () => {
    const group = { id: "g", memberIds: ["w:a", "w:b"], visibleIds: ["w:a", "w:b"], focusedId: "w:a" };
    await mount({ tabs: [web("a"), web("b"), web("c")], groups: [group], activeId: "w:a" });
    await openMenu("w:c");
    assert.equal(menuItem("Remove from group").disabled, true);
    await fire(document.body, "pointerdown");
    await openMenu("w:a");
    await click(menuItem("Remove from group"));
    assert.deepEqual(useCenterTabs.getState().groups, []);
    assert.equal(status(), "Tab removed from group");
  });
});

describe("New split view with this tab", () => {
  test("is enabled whenever another tab exists and opens the picker on the center body", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    const entry = menuItem("New split view with this tab");
    assert.equal(entry.disabled, false);
    await click(entry);
    assert.ok(!menu());
    const picker = document.querySelector(".center-body [role=dialog]");
    assert.ok(picker, "the picker is portalled onto the center body, outside the 40px strip");
    assert.equal(picker.getAttribute("aria-label"), "Choose a tab to add to split view");
  });

  test("is disabled for a lone tab and for a member of an existing split", async () => {
    await mount({ tabs: [web("a")], activeId: "w:a" });
    await openMenu("w:a");
    assert.equal(menuItem("New split view with this tab").disabled, true);
    await view.unmount();
    const group = { id: "g", memberIds: ["w:a", "w:b"], visibleIds: ["w:a", "w:b"], focusedId: "w:a" };
    await mount({ tabs: [web("a"), web("b"), web("c")], groups: [group], activeId: "w:a" });
    await openMenu("w:a");
    assert.equal(menuItem("New split view with this tab").disabled, true,
      "a composite tab cannot add another independently selectable member");
  });

  test("picking a tab groups it and announces the split", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    await click(menuItem("New split view with this tab"));
    const option = document.querySelector(".center-body [role=option]");
    await click(option);
    assert.deepEqual(useCenterTabs.getState().groups[0].memberIds.slice().sort(), ["w:a", "w:b"]);
    assert.equal(status(), "Tab added to split");
    assert.ok(!document.querySelector(".center-body [role=dialog]"));
  });
});

describe("Move to new window", () => {
  test("is disabled in a plain browser", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    assert.equal(menuItem("Move to new window").disabled, true);
  });

  test("prepares a transfer synchronously and detaches the token into a new window", async () => {
    const calls = [];
    window.openprogramDesktop = {
      windowId: "main",
      tabTransfer: {
        prepare: (payload) => { calls.push(["prepare", payload.tabs.map((tab) => tab.id), payload.source?.windowId]); return "token-1"; },
        detach: async (token) => { calls.push(["detach", token, dragCoordinator.current()]); return "window-2"; },
        cancel: async (token) => { calls.push(["cancel", token]); return true; },
      },
    };
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:b");
    await click(menuItem("Move to new window"));
    assert.deepEqual(calls, [["prepare", ["w:b"], "main"], ["detach", "token-1", null]],
      "the coordinator record is started and cleared before the detach resolves");
    assert.ok(!menu());
    assert.equal(status(), "Tab moved to new window");
  });

  test("announces a cancel when the main process refuses the transfer", async () => {
    window.openprogramDesktop = {
      windowId: "main",
      tabTransfer: { prepare: () => "token-2", detach: async () => null, cancel: async () => true },
    };
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:b");
    await click(menuItem("Move to new window"));
    assert.equal(status(), "Tab move cancelled");
  });
});

describe("desktop overlay menu", () => {
  test("the desktop shell opens the menu as a top-layer overlay and its actions dispatch back", async () => {
    const opened = [];
    let onAction;
    window.openprogramDesktop = {
      windowId: "main",
      tabTransfer: { prepare: () => null, cancel: async () => true },
      mainMenu: { open: (spec) => opened.push(spec), onAction: (callback) => { onAction = callback; return () => {}; } },
    };
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await openMenu("w:a");
    assert.ok(!menu(), "no in-DOM menu: native web views would paint over it");
    const ids = opened[0].items.map((item) => item.id);
    assert.deepEqual(ids, [
      "tabmenu:move-left", "tabmenu:move-right", "tabmenu:close", "tabmenu:close-others",
      "tabmenu:close-right", "tabmenu:split", "tabmenu:ungroup", "tabmenu:new-window",
    ]);
    assert.equal(opened[0].items[0].disabled, true);
    await view.act(async () => onAction("tabmenu:move-right"));
    assert.deepEqual(tabIds(), ["w:b", "w:a"]);
    await view.act(async () => onAction("other:move-right"));
    assert.deepEqual(tabIds(), ["w:b", "w:a"], "ids without the tabmenu: prefix belong to the main menu");
  });
});

describe("keyboard roving focus", () => {
  test("arrow keys, Home and End move focus between tabs without activating them", async () => {
    await mount({ tabs: [web("a"), web("b"), web("c")], activeId: "w:a" });
    const key = (id, value) => fire(tabTarget(id), "keydown", { key: value });
    await key("w:a", "ArrowRight");
    assert.ok(document.activeElement === tabTarget("w:b"));
    await key("w:b", "End");
    assert.ok(document.activeElement === tabTarget("w:c"));
    await key("w:c", "ArrowRight");
    assert.ok(document.activeElement === tabTarget("w:a"), "ArrowRight wraps");
    await key("w:a", "ArrowLeft");
    assert.ok(document.activeElement === tabTarget("w:c"));
    await key("w:c", "Home");
    assert.ok(document.activeElement === tabTarget("w:a"));
    assert.equal(useCenterTabs.getState().activeId, "w:a", "moving focus never activates a tab");
    await key("w:a", "ArrowRight");
    assert.equal(tabTarget("w:b").getAttribute("tabindex"), "0", "one roving tab stop follows focus");
    assert.equal(tabTarget("w:a").getAttribute("tabindex"), "-1");
  });

  test("Enter activates the focused tab", async () => {
    await mount({ tabs: [web("a"), web("b")], activeId: "w:a" });
    await fire(tabTarget("w:b"), "keydown", { key: "Enter" });
    assert.equal(useCenterTabs.getState().activeId, "w:b");
  });
});
