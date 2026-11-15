import assert from "node:assert/strict";
import test from "node:test";

import { centerTabStripEntries, placeDockedTabs } from "../../lib/tabs/center-tab-groups.ts";

const ids = (tabs) => tabs.map((tab) => tab.id);
const tabs = ["a", "b", "c", "d"].map((id) => ({ id }));

test("A merged split keeps the slot of the window it joined", () => {
  // b dropped on the left of d: the split's first pane is b.
  const ordered = placeDockedTabs(tabs, ["b"], "d");
  assert.deepEqual(ids(ordered), ["a", "c", "b", "d"]);
  const entries = centerTabStripEntries({ tabIds: ids(ordered), groups: [{ id: "g", memberIds: ["b", "d"], visibleIds: ["b", "d"], focusedId: "b" }] });
  assert.deepEqual(entries.map((e) => e.id), ["tab:a", "tab:c", "group:g"]);
});

test("Docking leaves the order alone when the anchor itself moves or is missing", () => {
  assert.deepEqual(ids(placeDockedTabs(tabs, ["d"], "d")), ["a", "b", "c", "d"]);
  assert.deepEqual(ids(placeDockedTabs(tabs, ["b"], "zz")), ["a", "b", "c", "d"]);
  assert.deepEqual(ids(placeDockedTabs(tabs, ["d", "a"], "b")), ["d", "a", "b", "c"]);
});
