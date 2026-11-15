import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseHTML } from "linkedom";

const { toastAnchor, toastCentreX } = await import("../../components/ui/toast-anchor.ts");

const source = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

/** A window 1512 wide: left rail to 280, centre card 288..1449. In a split
 *  the card holds a pane per tab; `boxes` gives each element's box. */
function page({ split = false, fullscreen = false, hiddenColumn = false } = {}) {
  const { document } = parseHTML(`<!doctype html><html${fullscreen ? " data-canvas-fullscreen" : ""}><body>
    <div class="app">
      <aside id="rail"></aside>
      <div class="center-col" id="column"${split ? " data-split" : ""}>
        <div class="center-body">
          <div id="left" data-center-tab-id="tab-a"></div>
          <div id="right" data-center-tab-id="tab-b"></div>
          <div id="file" data-file-pane-id="tab-f"></div>
          <div id="gone" data-center-tab-id="tab-c"></div>
        </div>
      </div>
      <div class="center-col page-col" id="page"></div>
    </div></body></html>`);
  const boxes = {
    column: hiddenColumn ? [0, 0, 0] : [288, 1161, 1400],
    page: [0, 0, 0],
    left: split ? [295, 570, 800] : [288, 1161, 800],
    right: split ? [873, 570, 800] : [0, 0, 0],
    file: split ? [873, 570, 400] : [0, 0, 0],
    gone: [0, 0, 0],
  };
  for (const [id, [left, width, height]] of Object.entries(boxes)) {
    document.getElementById(id).getBoundingClientRect = () => ({ left, width, height, top: 47, right: left + width, bottom: 47 + height });
  }
  return document;
}

const tabs = (activeId) => ({
  activeId,
  tabs: [
    { id: "tab-a", sessionId: "s-a" },
    { id: "tab-b", sessionId: "s-b" },
    { id: "tab-c", sessionId: "s-c" },
    { id: "tab-f" },
  ],
});

test("in a split view a toast centres over the active pane, not the window's divider", () => {
  const doc = page({ split: true });
  assert.equal(toastAnchor(doc, tabs("tab-a")).id, "left");
  assert.equal(toastAnchor(doc, tabs("tab-b")).id, "right");
  assert.equal(toastAnchor(doc, tabs("tab-f")).id, "file");
  const box = toastAnchor(doc, tabs("tab-a")).getBoundingClientRect();
  assert.equal(toastCentreX(box, 1512, 168), 295 + 570 / 2);
  assert.notEqual(toastCentreX(box, 1512, 168), 1512 / 2);
});

test("a toast about a conversation centres over the pane showing it", () => {
  const doc = page({ split: true });
  assert.equal(toastAnchor(doc, tabs("tab-a"), "s-b").id, "right");
  // That conversation is not in a visible pane: the active pane.
  assert.equal(toastAnchor(doc, tabs("tab-b"), "s-c").id, "right");
  assert.equal(toastAnchor(doc, tabs("tab-b"), "s-none").id, "right");
  // The active tab is in no visible pane: the centre column.
  assert.equal(toastAnchor(doc, tabs("tab-c")).id, "column");
  assert.equal(toastAnchor(doc, tabs(null)).id, "column");
});

test("without a split a toast centres over the centre column between the sidebars", () => {
  const doc = page();
  assert.equal(toastAnchor(doc, tabs("tab-a")).id, "column");
  assert.equal(toastAnchor(doc, tabs("tab-a"), "s-a").id, "column");
  assert.equal(toastCentreX(doc.getElementById("column").getBoundingClientRect(), 1512, 168), 288 + 1161 / 2);
});

test("full screen or no centre column centres on the window", () => {
  assert.equal(toastAnchor(page({ split: true, fullscreen: true }), tabs("tab-a")), null);
  assert.equal(toastAnchor(page({ hiddenColumn: true }), tabs("tab-a")), null);
  assert.equal(toastCentreX(null, 1512, 168), 756);
  assert.equal(toastCentreX({ left: 0, width: 0 }, 1512, 168), 756, "a closed pane counts as the window");
});

test("the bubble stays inside the window when its anchor is near an edge", () => {
  assert.equal(toastCentreX({ left: 500, width: 200 }, 700, 496), 700 - 248 - 8);
  assert.equal(toastCentreX({ left: 0, width: 100 }, 700, 300), 158);
  assert.equal(toastCentreX({ left: 0, width: 100 }, 300, 400), 150, "wider than the window: its centre");
});

test("the host places itself from the anchor and observes only while a toast shows", () => {
  const host = source("components/ui/toast-host.tsx");
  assert.match(host, /toastAnchor\(document, useCenterTabs\.getState\(\), newestSession\)/);
  assert.match(host, /host\.style\.left = `\$\{x\}px`/);
  assert.match(host, /if \(!host \|\| !newestId\) return;/);
  assert.match(host, /new ResizeObserver\(place\)/);
  assert.match(host, /return \(\) => observer\.disconnect\(\);/);
  assert.doesNotMatch(host, /requestAnimationFrame/);
  assert.match(source("lib/format-utils/toast.ts"), /sessionId: opts\.sessionId/);
  const rename = source("lib/chat/session-auto-rename.ts");
  assert.equal((rename.match(/showToast\(/g) ?? []).length, (rename.match(/sessionId \}\);/g) ?? []).length);
});

test("a long message wraps inside the bubble instead of running past its edge", () => {
  const css = source("components/ui/toast-host.module.css");
  const toast = css.match(/\.toast \{[^}]*\}/)[0];
  assert.doesNotMatch(toast, /white-space:\s*nowrap/);
  assert.match(toast, /max-width:/);
  assert.match(css, /\.message \{[^}]*overflow-wrap: anywhere/);
  assert.match(source("components/ui/toast-host.tsx"), /className=\{styles\.message\}/);
});
