import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader } from "../_support/load.mjs";

installLoader();
const { groupHistoryByLocalDate } = await import("@/lib/chat/history/history-groups");

const DAY = 86_400_000;

test("history entries group by local calendar day in the order given, dropping invalid times", () => {
  const groups = groupHistoryByLocalDate([
    { id: "new-a", visitedAt: Date.UTC(2026, 7, 15, 12) },
    { id: "new-b", visitedAt: Date.UTC(2026, 7, 15, 8) },
    { id: "old", visitedAt: Date.UTC(2026, 7, 15, 8) - DAY },
    { id: "invalid", visitedAt: Number.NaN },
  ]);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map((group) => group.entries.map((entry) => entry.id)), [["new-a", "new-b"], ["old"]]);
});

test("a group key is the zero-padded local date", () => {
  const visitedAt = new Date(2026, 0, 5, 9).getTime();
  const [group] = groupHistoryByLocalDate([{ visitedAt }]);
  assert.equal(group.key, "2026-01-05");
  assert.equal(group.date.getTime(), visitedAt);
});
