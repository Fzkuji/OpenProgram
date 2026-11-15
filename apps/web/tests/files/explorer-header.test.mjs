import assert from "node:assert/strict";
import { afterEach, describe, mock, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/lib/clipboard": "export const copyText = async (value) => { (globalThis.__copied ??= []).push(value); return globalThis.__copyResult ?? true; };",
    "@/components/solar-icons": "export const SolarIcon = () => null;",
  },
  replace: {
    // The tip's label is the copy feedback; expose it as an attribute.
    "/components/ui/tooltip.tsx": "import { createElement } from 'react'; export const HoverTip = ({ label, children }) => createElement('span', { 'data-tip': label }, children);",
  },
});
installDom();

const { createElement, useState } = await import("react");
const { ExplorerHeader, ExplorerMatchText } = await import("../../components/files/explorer/explorer-header.tsx");

const moves = [];
/** ExplorerHeader with its search state held the way the Files and Programs panes hold it. */
function Harness({ resultCount = 0, resultIndex = 0, initial = {}, ...props }) {
  const [searchOpen, setSearchOpen] = useState(initial.searchOpen ?? false);
  const [query, setQuery] = useState(initial.query ?? "");
  const [mode, setMode] = useState("filter");
  const [fuzzy, setFuzzy] = useState(false);
  return createElement("div", { "data-mode": mode, "data-fuzzy": String(fuzzy), "data-query": query },
    createElement(ExplorerHeader, {
      rootName: "project",
      rootPath: "/Users/me/project",
      searchOpen, onSearchOpenChange: setSearchOpen,
      query, onQueryChange: setQuery,
      mode, onModeChange: setMode,
      fuzzy, onFuzzyChange: setFuzzy,
      resultCount, resultIndex,
      onMoveResult: (delta) => moves.push(delta),
      ...props,
    }));
}

const fire = (view, node, type, extra = {}) =>
  view.act(() => { node.dispatchEvent(Object.assign(new window.Event(type, { bubbles: true }), extra)); });
const click = (view, node) => fire(view, node, "click");
const byLabel = (container, label) => container.querySelector(`[aria-label="${label}"]`);
const buttonText = (container, label) => [...container.querySelectorAll("button")].find((node) => node.textContent.trim() === label);
const state = (container) => container.firstElementChild.dataset;
const flush = (view) => view.act(async () => { await Promise.resolve(); await Promise.resolve(); });

let view;
afterEach(async () => {
  mock.timers.reset();
  await view?.unmount();
  view = null;
  moves.length = 0;
  globalThis.__copied = [];
  globalThis.__copyResult = true;
});

describe("explorer header root path", () => {
  test("the root name reveals the full path on click and after a 1.5 s hover", async () => {
    view = await render(createElement(Harness));
    const root = buttonText(view.container, "project");
    assert.equal(root.getAttribute("aria-expanded"), "false");
    assert.match(view.container.textContent, /\/Users\/me\/project/);
    await click(view, root);
    assert.equal(root.getAttribute("aria-expanded"), "true");
    await click(view, root);
    assert.equal(root.getAttribute("aria-expanded"), "false");

    mock.timers.enable({ apis: ["setTimeout"] });
    await fire(view, root, "mouseover");
    await view.act(() => mock.timers.tick(1499));
    assert.equal(root.getAttribute("aria-expanded"), "false");
    await view.act(() => mock.timers.tick(1));
    assert.equal(root.getAttribute("aria-expanded"), "true");
  });

  test("copy path copies the root path and says Copied until it resets", async () => {
    view = await render(createElement(Harness));
    const copy = byLabel(view.container, "Copy path");
    assert.equal(copy.disabled, false);
    assert.equal(copy.closest("[data-tip]").dataset.tip, "Copy path");
    mock.timers.enable({ apis: ["setTimeout"] });
    await click(view, copy);
    await flush(view);
    assert.deepEqual(globalThis.__copied, ["/Users/me/project"]);
    assert.equal(copy.closest("[data-tip]").dataset.tip, "Copied");
    await view.act(() => mock.timers.tick(1200));
    assert.equal(copy.closest("[data-tip]").dataset.tip, "Copy path");
  });

  test("a failed copy gives no Copied feedback, and there is nothing to copy without a path", async () => {
    globalThis.__copyResult = false;
    view = await render(createElement(Harness));
    await click(view, byLabel(view.container, "Copy path"));
    await flush(view);
    assert.equal(byLabel(view.container, "Copy path").closest("[data-tip]").dataset.tip, "Copy path");
    await view.rerender(createElement(Harness, { rootPath: null }));
    assert.equal(byLabel(view.container, "Copy path").disabled, true);
    assert.match(view.container.textContent, /project/, "the root name stands in for a missing path");
  });

  test("showRootPath false (the Programs pane) shows no root row", async () => {
    view = await render(createElement(Harness, { showRootPath: false }));
    assert.equal(buttonText(view.container, "project"), undefined);
    assert.equal(byLabel(view.container, "Copy path"), null);
    assert.doesNotMatch(view.container.textContent, /\/Users\/me\/project/);
    assert.ok(byLabel(view.container, "Search"));
  });
});

describe("explorer header search", () => {
  test("the search toggle reports aria-expanded and opens a focused field", async () => {
    view = await render(createElement(Harness));
    const toggle = byLabel(view.container, "Search");
    assert.equal(toggle.tagName, "BUTTON");
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    const panel = view.container.querySelector("input").closest("[aria-hidden]");
    assert.equal(panel.getAttribute("aria-hidden"), "true");
    assert.equal(view.container.querySelector("input").tabIndex, -1);
    // linkedom keeps no focus state; record focus() calls instead.
    const focused = [];
    const proto = Object.getPrototypeOf(view.container.querySelector("input"));
    const ownFocus = Object.getOwnPropertyDescriptor(proto, "focus");
    proto.focus = function focus() { focused.push(this); };
    try {
      await click(view, toggle);
    } finally {
      if (ownFocus) Object.defineProperty(proto, "focus", ownFocus); else delete proto.focus;
    }
    const close = byLabel(view.container, "Close search");
    assert.ok(close === toggle, "the same button now closes search");
    assert.equal(close.getAttribute("aria-expanded"), "true");
    assert.equal(panel.getAttribute("aria-hidden"), "false");
    assert.ok(focused.length === 1 && focused[0] === view.container.querySelector("input"), "the opened field takes focus");
  });

  test("Escape in the field closes search and clears the query; so does the close button", async () => {
    view = await render(createElement(Harness, { initial: { searchOpen: true, query: "paper" } }));
    const input = view.container.querySelector("input");
    assert.equal(input.value, "paper");
    await fire(view, input, "keydown", { key: "Escape" });
    assert.equal(state(view.container).query, "");
    assert.equal(byLabel(view.container, "Search").getAttribute("aria-expanded"), "false");
    await view.unmount();

    view = await render(createElement(Harness, { initial: { searchOpen: true, query: "paper" } }));
    await click(view, byLabel(view.container, "Close search"));
    assert.equal(state(view.container).query, "");
    assert.equal(byLabel(view.container, "Search").getAttribute("aria-expanded"), "false");
  });

  test("Filter and Highlight are exclusive display modes and Fuzzy toggles on its own", async () => {
    view = await render(createElement(Harness, { initial: { searchOpen: true } }));
    const filter = buttonText(view.container, "Filter");
    const highlight = buttonText(view.container, "Highlight");
    const fuzzy = buttonText(view.container, "Fuzzy");
    assert.equal(filter.getAttribute("aria-pressed"), "true");
    assert.equal(highlight.getAttribute("aria-pressed"), "false");
    await click(view, highlight);
    assert.equal(state(view.container).mode, "highlight");
    assert.equal(highlight.getAttribute("aria-pressed"), "true");
    assert.equal(filter.getAttribute("aria-pressed"), "false");
    await click(view, highlight);
    assert.equal(state(view.container).mode, "highlight", "pressing the active mode keeps it");
    await click(view, filter);
    assert.equal(state(view.container).mode, "filter");

    assert.equal(fuzzy.getAttribute("aria-pressed"), "false");
    await click(view, fuzzy);
    assert.equal(state(view.container).fuzzy, "true");
    assert.equal(fuzzy.getAttribute("aria-pressed"), "true");
    assert.equal(state(view.container).mode, "filter");
  });

  test("previous and next match move through results by button and arrow key", async () => {
    view = await render(createElement(Harness, { initial: { searchOpen: true, query: "pa" }, resultCount: 3, resultIndex: 1 }));
    assert.match(view.container.querySelector('[aria-live="polite"]').textContent, /^2 \/ 3$/);
    await click(view, byLabel(view.container, "Next match"));
    await click(view, byLabel(view.container, "Previous match"));
    const input = view.container.querySelector("input");
    await fire(view, input, "keydown", { key: "ArrowDown" });
    await fire(view, input, "keydown", { key: "ArrowUp" });
    assert.deepEqual(moves, [1, -1, 1, -1]);
  });

  test("with no results the counter reads 0 / 0 and match navigation is off", async () => {
    view = await render(createElement(Harness, { initial: { searchOpen: true, query: "zzz" } }));
    assert.match(view.container.querySelector('[aria-live="polite"]').textContent, /^0 \/ 0$/);
    assert.equal(byLabel(view.container, "Next match").disabled, true);
    assert.equal(byLabel(view.container, "Previous match").disabled, true);
    await fire(view, view.container.querySelector("input"), "keydown", { key: "ArrowDown" });
    assert.deepEqual(moves, []);
    await click(view, byLabel(view.container, "Clear"));
    assert.equal(state(view.container).query, "");
    assert.equal(byLabel(view.container, "Clear").disabled, true);
  });

  test("hideSearch leaves out the search toggle and panel", async () => {
    view = await render(createElement(Harness, { hideSearch: true }));
    assert.equal(byLabel(view.container, "Search"), null);
    assert.equal(view.container.querySelector("input"), null);
  });
});

describe("explorer match text", () => {
  test("wraps each run of matched characters and leaves the rest as text", async () => {
    view = await render(createElement(ExplorerMatchText, { value: "templates", query: "pa", fuzzy: true, current: true }));
    const marks = [...view.container.querySelectorAll(".treeNameMatch")];
    assert.deepEqual(marks.map((node) => node.textContent), ["p", "a"]);
    assert.ok(marks.every((node) => node.classList.contains("treeNameMatchCurrent")));
    assert.equal(view.container.textContent, "templates");
    await view.rerender(createElement(ExplorerMatchText, { value: "paper_search", query: "paper", fuzzy: false }));
    assert.deepEqual([...view.container.querySelectorAll(".treeNameMatch")].map((node) => node.textContent), ["paper"]);
    await view.rerender(createElement(ExplorerMatchText, { value: "tools", query: "pa", fuzzy: false }));
    assert.equal(view.container.querySelector(".treeNameMatch"), null);
  });
});
