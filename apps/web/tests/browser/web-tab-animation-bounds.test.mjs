import test from "node:test";
import assert from "node:assert/strict";
import { isMovedByAnimation } from "../../lib/browser/web-tab/web-tab-bounds.ts";


function node(parent = null) {
  return { parent, contains(other) { for (let n = other; n; n = n.parent) if (n === this) return true; return false; } };
}
function anim(target, frames, { playState = "running", iterations = 1 } = {}) {
  return { playState, effect: { target, getTiming: () => ({ iterations }), getKeyframes: () => frames } };
}

test("a running zoom on an ancestor moves the web tab", () => {
  const card = node(), pane = node(node(card));
  assert.equal(isMovedByAnimation(pane, [anim(card, [{ transform: "scale(0.3)" }, { transform: "none" }])]), true);
});

test("finished, unrelated, endless or paint-only animations do not", () => {
  const card = node(), pane = node(card), other = node();
  const zoom = [{ transform: "scale(0.3)" }, { transform: "none" }];
  assert.equal(isMovedByAnimation(pane, [anim(card, zoom, { playState: "finished" })]), false);
  assert.equal(isMovedByAnimation(pane, [anim(other, zoom)]), false);
  assert.equal(isMovedByAnimation(pane, [anim(card, zoom, { iterations: Infinity })]), false);
  assert.equal(isMovedByAnimation(pane, [anim(card, [{ opacity: 0 }, { opacity: 1 }])]), false);
});
