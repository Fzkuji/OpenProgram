/**
 * SplitViewPicker (components/center-tabs/canvas/split-view-picker.tsx):
 * Chrome's "New split view with this tab" picker, rendered against the real
 * center-tab store.
 */
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/components/animated-icons": [
      "import { createElement } from 'react';",
      "const Icon = () => createElement('svg', { 'data-icon': 'animated' });",
      "export const ChromeIcon = Icon, FeatherIcon = Icon;",
    ].join("\n"),
    "@/components/files/file-type-icon": "export const FileTypeIcon = () => null;",
    "@/components/applications/application-glyph": "export const ApplicationTabGlyph = () => null;",
  },
});
installDom({ pathname: "/chat" });
// linkedom does not track focus: remember the last focused element.
let focused = null;
window.HTMLElement.prototype.focus = function focus() { focused = this; };
Object.defineProperty(document, "activeElement", { get: () => focused, configurable: true });

const { createElement: h } = await import("react");
const { SplitViewPicker } = await import("../../components/center-tabs/canvas/split-view-picker.tsx");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");

const tabs = [
  { id: "s:a", kind: "session", title: "Chat A", sessionId: "a" },
  { id: "w:b", kind: "web", title: "Docs", url: "https://docs.example.com/guide" },
  { id: "f:c", kind: "file", title: "c.ts", path: "/p/c.ts" },
];

let view;
let closes;
let picks;
async function mount(subjectId = "s:a", state = {}) {
  useCenterTabs.setState({ tabs, groups: [], activeId: "s:a", ...state });
  closes = [];
  picks = [];
  focused = null;
  view = await render(h(SplitViewPicker, {
    subjectId,
    titleOf: (tab) => `title:${tab.title}`,
    onClose: (reason) => closes.push(reason),
    onPicked: (accepted) => picks.push(accepted),
  }));
  return view;
}
const options = () => [...view.container.querySelectorAll('[role="option"]')];
async function key(target, value) {
  await view.act(async () => {
    const event = new window.Event("keydown", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "key", { value });
    target.dispatchEvent(event);
  });
}

afterEach(async () => {
  await view?.unmount();
  view = undefined;
});

describe("SplitViewPicker", () => {
  test("is a labelled modal dialog listing every other tab as an option", async () => {
    await mount();
    const dialog = view.container.querySelector('[role="dialog"]');
    assert.equal(dialog.getAttribute("aria-label"), "Choose a tab to add to split view");
    assert.equal(dialog.getAttribute("aria-modal"), "true");
    assert.ok(view.container.querySelector('[role="listbox"]'));
    assert.deepEqual(options().map((option) => option.textContent), [
      "title:Docsdocs.example.com",
      "title:c.ts/p/c.ts",
    ], "titles come from the strip's labels; web tabs show their host, files their path");
    assert.equal(view.container.querySelector("[data-split-close]").getAttribute("aria-label"), "Close");
  });

  test("focuses the first option, and the Close button when there is nothing to pick", async () => {
    await mount();
    assert.ok(focused === options()[0]);
    await view.unmount();
    await mount("s:a", { tabs: [tabs[0]] });
    assert.equal(options().length, 0);
    assert.match(view.container.textContent, /No other tabs to split with/);
    assert.ok(focused === view.container.querySelector("[data-split-close]"));
  });

  test("ArrowDown and ArrowUp move a single roving selection and wrap", async () => {
    await mount();
    const dialog = view.container.querySelector('[role="dialog"]');
    await key(dialog, "ArrowDown");
    assert.ok(focused === options()[1]);
    assert.deepEqual(options().map((option) => option.getAttribute("aria-selected")), ["false", "true"]);
    assert.deepEqual(options().map((option) => option.getAttribute("tabindex")), ["-1", "0"]);
    await key(dialog, "ArrowDown");
    assert.ok(focused === options()[0], "ArrowDown wraps to the first option");
    await key(dialog, "ArrowUp");
    assert.ok(focused === options()[1], "ArrowUp wraps to the last option");
  });

  test("Escape, the Close button and an outside press each close with their reason", async () => {
    await mount();
    await key(document.body, "Escape");
    await view.act(async () => view.container.querySelector("[data-split-close]").click());
    await view.act(async () => {
      options()[0].dispatchEvent(new window.Event("pointerdown", { bubbles: true }));
    });
    assert.deepEqual(closes, ["escape", "close-button"], "a press inside the picker does not close it");
    await view.act(async () => {
      document.body.dispatchEvent(new window.Event("pointerdown", { bubbles: true }));
    });
    assert.deepEqual(closes, ["escape", "close-button", "outside"]);
  });

  test("choosing a tab groups it with the subject through the store", async () => {
    await mount();
    await view.act(async () => options()[0].click());
    assert.deepEqual(picks, [true]);
    const [group] = useCenterTabs.getState().groups;
    assert.deepEqual(group.memberIds, ["s:a", "w:b"]);
  });

  test("a stale row is revalidated against the latest store before grouping", async () => {
    await mount();
    const staleOption = options()[0];
    let before;
    await view.act(async () => {
      // Another operation grouped the row's tab; the click lands before React re-renders.
      useCenterTabs.getState().groupTab("f:c", "w:b", 1, "g:other");
      before = useCenterTabs.getState().groups;
      staleOption.click();
    });
    assert.deepEqual(picks, [false], "the picker reports the rejection");
    assert.deepEqual(useCenterTabs.getState().groups, before, "nothing moves");
  });

  test("a subject that has closed in the meantime cannot be grouped", async () => {
    await mount();
    const option = options()[0];
    await view.act(async () => {
      useCenterTabs.setState({ tabs: tabs.slice(1) });
      option.click();
    });
    assert.deepEqual(picks, [false]);
    assert.deepEqual(useCenterTabs.getState().groups, []);
  });
});
