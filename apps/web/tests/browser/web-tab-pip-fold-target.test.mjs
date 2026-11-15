import test from "node:test";
import assert from "node:assert/strict";
import { foldTarget } from "../../lib/browser/web-tab/web-tab-pip-motion.ts";

/** A chip in the pane of the given tab, with the given on-screen size,
 *  naming the given page. */
function chip(paneTabId, width, height, page = "w:page") {
  const pane = { dataset: { centerTabId: paneTabId } };
  return {
    pane,
    dataset: { webSurfaceChip: page },
    getBoundingClientRect: () => ({ width, height }),
    closest: (selector) => (selector === "[data-center-tab-id]" ? pane : null),
  };
}

function withDocument(chips, ownerTabId, run) {
  const previous = globalThis.document;
  globalThis.document = {
    querySelectorAll: (selector) => (selector === "[data-web-surface-chip]" ? chips : []),
    querySelector: (selector) => (selector === '[data-pip="true"]' && ownerTabId ? { dataset: { pipOwnerTabId: ownerTabId } } : null),
  };
  try { return run(); } finally { globalThis.document = previous; }
}

test("the preview folds into the chip on screen, not the hidden single-pane chat's 0×0 chip", () => {
  // Split view keeps the single-pane chat mounted but hidden; its chip comes
  // first in the document. The fold used to head for it, the window's corner.
  const hidden = chip("single", 0, 0);
  const visible = chip("right", 149, 28);
  assert.equal(withDocument([hidden, visible], "right", () => foldTarget("w:page")), visible);
});

test("among visible chips the one in the preview's own pane wins", () => {
  const left = chip("left", 149, 28);
  const right = chip("right", 149, 28);
  assert.equal(withDocument([left, right], "right", () => foldTarget("w:page")), right);
  assert.equal(withDocument([left, right], null, () => foldTarget("w:page")), left);
});

test("no visible chip, no fold target", () => {
  assert.equal(withDocument([chip("single", 0, 0)], "right", () => foldTarget("w:page")), null);
});

test("the preview folds only into a chip that names its own page", () => {
  // In a split view the chat's chip names the page in the other pane; the
  // preview of another page folded into it, and that chip could not unfold it.
  const other = chip("right", 149, 28, "w:split-page");
  assert.equal(withDocument([other], "right", () => foldTarget("w:page")), null);
  const own = chip("left", 149, 28, "w:page");
  assert.equal(withDocument([other, own], "right", () => foldTarget("w:page")), own);
  assert.equal(withDocument([own], "right", () => foldTarget(null)), null);
});
