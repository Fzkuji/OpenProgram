/**
 * The tab strip's pure geometry (components/center-tabs/strip/tab-strip-geometry.ts)
 * and how an active tab keeps itself visible inside the strip (TabItem).
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/components/animated-icons": [
      "import { forwardRef, createElement } from 'react';",
      "const Icon = forwardRef((props, ref) => createElement('svg', { 'data-icon': 'animated' }));",
      "export const ChromeIcon = Icon, FeatherIcon = Icon, MessageCircleIcon = Icon;",
    ].join("\n"),
    "@/components/files/file-type-icon": "export const FileTypeIcon = () => null;",
    "@/components/applications/application-glyph": "export const ApplicationTabGlyph = () => null;",
  },
});
installDom({ pathname: "/chat" });
globalThis.CSS = { escape: (value) => String(value).replaceAll(":", "\\:") };

const geometry = await import("../../components/center-tabs/strip/tab-strip-geometry.ts");
const { centerTabStripEntries } = await import("../../lib/tabs/center-tab-groups.ts");
const { useCenterTabs } = await import("../../lib/tabs/center-tabs-store.ts");
const { createElement: h } = await import("react");
const { TabItem } = await import("../../components/center-tabs/strip/tab-items.tsx");

const entriesFor = (tabIds, groups = []) => centerTabStripEntries({ tabIds, groups });

describe("computeLiveShifts", () => {
  const entries = entriesFor(["a", "b", "c", "d"]);
  const step = 100 + geometry.STRIP_GAP;

  test("dragging right slides every tab between source and target left by one slot", () => {
    const shifts = geometry.computeLiveShifts(entries, new Set(["a"]), { mode: "after", targetTabId: "c" }, 100);
    assert.deepEqual(Object.fromEntries(shifts), { "tab:b": -step, "tab:c": -step });
  });

  test("dragging left slides the crossed tabs right by one slot", () => {
    const shifts = geometry.computeLiveShifts(entries, new Set(["d"]), { mode: "before", targetTabId: "b" }, 100);
    assert.deepEqual(Object.fromEntries(shifts), { "tab:b": step, "tab:c": step });
  });

  test("an insertion next to the source's own slot shifts nothing", () => {
    for (const marker of [{ mode: "before", targetTabId: "b" }, { mode: "after", targetTabId: "a" }]) {
      assert.equal(geometry.computeLiveShifts(entries, new Set(["b"]), marker, 100).size, 0);
    }
  });

  test("no marker, a merge intent or a zero width shifts nothing", () => {
    assert.equal(geometry.computeLiveShifts(entries, new Set(["a"]), null, 100).size, 0);
    assert.equal(geometry.computeLiveShifts(entries, new Set(["a"]), { mode: "merge", targetTabId: "c" }, 100).size, 0);
    assert.equal(geometry.computeLiveShifts(entries, new Set(["a"]), { mode: "after", targetTabId: "c" }, 0).size, 0);
  });

  test("a drag from another window opens a gap at the insertion point", () => {
    const shifts = geometry.computeLiveShifts(entries, new Set(["x"]), { mode: "before", targetTabId: "c" }, 100);
    assert.deepEqual(Object.fromEntries(shifts), { "tab:c": step, "tab:d": step });
  });

  test("a whole group moves as one entry and a segment over its own group shifts nothing", () => {
    const groups = [{ id: "g", memberIds: ["b", "c"], visibleIds: ["b", "c"], focusedId: "b" }];
    const grouped = entriesFor(["a", "b", "c", "d"], groups);
    const whole = geometry.computeLiveShifts(grouped, new Set(["b", "c"]), { mode: "after", targetTabId: "d" }, 100);
    assert.deepEqual(Object.fromEntries(whole), { "tab:d": -step });
    const segment = geometry.computeLiveShifts(grouped, new Set(["b"]), { mode: "after", targetTabId: "c" }, 100);
    assert.equal(segment.size, 0);
  });
});

describe("shiftStyle", () => {
  test("a shifted entry moves by transform only", () => {
    assert.deepEqual(geometry.shiftStyle(-108), { transform: "translateX(-108px)" });
  });

  test("an unshifted entry gets no style key, so the dragged tab's live offset survives re-renders", () => {
    assert.equal(geometry.shiftStyle(0), undefined);
  });
});

describe("slotOverlapRatio", () => {
  test("measures the covered fraction of the neighbour, not of the dragged tab", () => {
    assert.equal(geometry.slotOverlapRatio({ left: 100, width: 200 }, { left: 200, width: 50 }), 0.25);
    assert.equal(geometry.slotOverlapRatio({ left: 100, width: 50 }, { left: 0, width: 400 }), 1);
  });

  test("no overlap or an empty slot is zero", () => {
    assert.equal(geometry.slotOverlapRatio({ left: 100, width: 100 }, { left: 300, width: 50 }), 0);
    assert.equal(geometry.slotOverlapRatio({ left: 100, width: 0 }, { left: 100, width: 50 }), 0);
  });
});

describe("pickPointerDropTarget", () => {
  const targets = [
    { tabId: "a", left: 0, width: 100 },
    { tabId: "b", left: 110, width: 100 },
    { tabId: "c", left: 220, width: 100 },
  ];

  test("the slot containing the centre wins", () => {
    assert.equal(geometry.pickPointerDropTarget(targets, 150)?.tabId, "b");
  });

  test("between slots the nearest edge wins", () => {
    assert.equal(geometry.pickPointerDropTarget(targets, 104)?.tabId, "a");
    assert.equal(geometry.pickPointerDropTarget(targets, 107)?.tabId, "b");
    assert.equal(geometry.pickPointerDropTarget(targets, 900)?.tabId, "c");
  });

  test("no slots means no target", () => {
    assert.equal(geometry.pickPointerDropTarget([], 10), null);
  });
});

describe("visibleStripBounds", () => {
  test("desktop uses the flow's visible client box, not its scrolled content width", () => {
    const flow = document.createElement("div");
    setRect(flow, { left: 90, width: 2000, height: 40 });
    flow.getClientRects = () => [{}];
    Object.defineProperty(flow, "clientWidth", { value: 600 });
    assert.deepEqual(geometry.visibleStripBounds(flow, null), { left: 90, right: 690 });
  });

  test("browser mode (flow is display:contents) falls back to the strip's padded content box", () => {
    const flow = document.createElement("div");
    flow.getClientRects = () => [];
    const strip = document.createElement("div");
    setRect(strip, { left: 0, width: 800, height: 40 });
    const previous = globalThis.getComputedStyle;
    globalThis.getComputedStyle = () => ({ paddingLeft: "12px", paddingRight: "20px" });
    try {
      assert.deepEqual(geometry.visibleStripBounds(flow, strip), { left: 12, right: 780 });
    } finally {
      globalThis.getComputedStyle = previous;
    }
    assert.equal(geometry.visibleStripBounds(null, null), null);
  });
});

describe("collectPointerDropTargets", () => {
  test("reads each entry's static slot, with group members carrying their group and index", () => {
    useCenterTabs.setState({
      tabs: [
        { id: "s:a", kind: "session", title: "A", sessionId: "a" },
        { id: "w:b", kind: "web", title: "B", url: "https://b.test/" },
        { id: "w:c", kind: "web", title: "C", url: "https://c.test/" },
      ],
      groups: [{ id: "g", memberIds: ["w:b", "w:c"], visibleIds: ["w:b", "w:c"], focusedId: "w:b" }],
      activeId: "s:a",
    });
    const flow = document.createElement("div");
    document.body.append(flow);
    for (const [id, left] of [["s:a", 0], ["w:b", 110], ["w:c", 220]]) {
      const root = document.createElement("div");
      const inner = document.createElement("div");
      inner.dataset.tabId = id;
      root.append(inner);
      setRect(root, { left, width: 100, height: 32 });
      flow.append(root);
    }
    try {
      assert.deepEqual(geometry.collectPointerDropTargets(flow), [
        { tabId: "s:a", left: 0, width: 100 },
        { tabId: "w:b", groupId: "g", memberIndex: 1, left: 110, width: 100 },
        { tabId: "w:c", groupId: "g", memberIndex: 2, left: 220, width: 100 },
      ]);
    } finally {
      flow.remove();
    }
  });
});

describe("TabItem keeps the active tab visible inside the strip", () => {
  const tab = { id: "w:far", kind: "web", title: "Far", url: "https://far.test/" };
  const noop = () => {};
  const props = (active) => ({
    tab, active, tabStop: active, enter: false, closing: false, label: "Far", closeLabel: "Close",
    onActivate: noop, onFocusTab: noop, onOpenMenu: noop, onClose: noop, onExited: noop,
    dragSubject: { kind: "tab", tabIds: [tab.id] }, onDragPointerDown: noop,
  });

  test("activating a tab past the right edge scrolls the strip itself, never scrollIntoView", async () => {
    const flow = document.createElement("div");
    flow.setAttribute("role", "tablist");
    document.body.append(flow);
    Object.defineProperty(flow, "offsetLeft", { value: 100, configurable: true });
    Object.defineProperty(flow, "clientWidth", { value: 400, configurable: true });
    flow.scrollLeft = 0;
    let scrolledIntoView = 0;
    const previous = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = () => { scrolledIntoView += 1; };
    const geometryOf = (element) => {
      Object.defineProperty(element, "offsetLeft", { value: 800, configurable: true });
      Object.defineProperty(element, "offsetWidth", { value: 200, configurable: true });
    };
    const view = await render(h(TabItem, props(false)), { container: flow });
    try {
      geometryOf(flow.firstElementChild);
      await view.rerender(h(TabItem, props(true)));
      // tab spans 700..900 relative to the flow; the 400px viewport ends at 900.
      assert.equal(flow.scrollLeft, 500);
      assert.equal(scrolledIntoView, 0, "scrollIntoView would also scroll every ancestor");

      // The strip resizing (window or sidebar) re-reveals the active tab.
      flow.scrollLeft = 0;
      Object.defineProperty(flow, "clientWidth", { value: 300, configurable: true });
      resizeObservers.trigger(flow);
      assert.equal(flow.scrollLeft, 600);

      // A tab already left of the viewport scrolls the strip back to its start.
      flow.scrollLeft = 850;
      resizeObservers.trigger(flow);
      assert.equal(flow.scrollLeft, 700);
      assert.equal(scrolledIntoView, 0);
    } finally {
      await view.unmount();
      flow.remove();
      window.HTMLElement.prototype.scrollIntoView = previous;
    }
  });
});
