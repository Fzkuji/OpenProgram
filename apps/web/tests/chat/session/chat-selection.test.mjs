import assert from "node:assert/strict";
import test from "node:test";

import { EMPTY_SELECTION, pickChat } from "../../../lib/sidebar/chat-selection.ts";

const order = ["a", "b", "c", "d", "e"];

test("a first Cmd-click picks the open chat and the clicked one", () => {
  assert.deepEqual(pickChat(order, EMPTY_SELECTION, "b", "d", "toggle"), { ids: ["b", "d"], anchor: "d" });
});

test("Cmd-click on a picked chat drops it", () => {
  assert.deepEqual(pickChat(order, { ids: ["b", "d"], anchor: "d" }, "b", "b", "toggle"), { ids: ["d"], anchor: "b" });
});

test("Shift-click takes the run from the anchor, either direction", () => {
  assert.deepEqual(pickChat(order, { ids: ["d"], anchor: "d" }, null, "b", "range").ids, ["b", "c", "d"]);
  assert.deepEqual(pickChat(order, EMPTY_SELECTION, "a", "c", "range").ids, ["a", "b", "c"]);
});

test("a chat that is not listed starts nothing", () => {
  assert.deepEqual(pickChat(order, EMPTY_SELECTION, "zz", "c", "toggle"), { ids: ["c"], anchor: "c" });
});
