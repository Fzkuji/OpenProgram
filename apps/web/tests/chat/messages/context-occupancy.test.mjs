// The ring, its tip and the /context card read one occupancy: the card's
// snapshot, else the session's last context_stats. A window that is not
// known is never made up: the count alone, no percentage.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";

installLoader();
const { contextTip, occupancyOf, occupancySnapshot, statsSnapshot } = await import("../../../lib/chat/context-occupancy.ts");

test("the tip of a new chat says the estimate the card shows", () => {
  const snapshot = occupancySnapshot({ total_used: 9_200, window: 500_000, basis: "estimated" }, undefined, undefined);
  assert.equal(contextTip(occupancyOf(snapshot), "openai-codex · gpt-6.1-terra"),
    "Context 9.2k / 500.0k (2%) · est. · openai-codex · gpt-6.1-terra");
});

test("once context_stats arrive they give the total and window; the snapshot keeps its categories", () => {
  const snapshot = occupancySnapshot({ total_used: 9_200, window: 500_000, basis: "estimated", tools: [] },
    { total_used: 50_000, basis: "measured" }, 500_000);
  assert.deepEqual(occupancyOf(snapshot), { used: 50_000, window: 500_000, basis: "measured" });
  assert.deepEqual(snapshot.tools, []);
  assert.equal(snapshot.free_space, 450_000);
  // An unknown window clears the snapshot's: no made-up limit.
  assert.equal(occupancyOf(occupancySnapshot({ total_used: 1, window: 10 }, { total_used: 134_300 }, undefined)).window, 0);
});

test("with no snapshot the session's stats stand in", () => {
  const snapshot = occupancySnapshot(null, { total_used: 40_000, basis: "measured" }, 200_000);
  assert.deepEqual(occupancyOf(snapshot), { used: 40_000, window: 200_000, basis: "measured" });
  // An older record without total_used: the last request's prompt size.
  assert.equal(occupancyOf(statsSnapshot({ input: 1_000, cache_read: 500 }, 0)).used, 1_500);
  assert.equal(occupancySnapshot(null, undefined, undefined), null);
});

test("an unknown window is the count alone, never a limit", () => {
  const occupancy = occupancyOf({ total_used: 134_300, window: null, context_window: null });
  assert.deepEqual(occupancy, { used: 134_300, window: 0, basis: null });
  assert.equal(contextTip(occupancy), "Context 134.3k");
});

test("a window with no count is nothing used, not a full ring", () => {
  assert.equal(occupancyOf(statsSnapshot(undefined, 200_000)).used, 0);
  assert.equal(occupancyOf({ window: 200_000 }).used, 0);
  assert.equal(occupancyOf({ window: 200_000, free_space: 150_000 }).used, 50_000);
});
