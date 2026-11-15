// Recency bucketing shared by the sidebar Recents list and the /chats page:
// both bucket by last activity on calendar days, never by creation time.
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";

installLoader({
  replace: {
    "/components/chat/messages/attachments/user-attachments.tsx": "export const parseAttachments = (text) => ({ text, attachments: [] });",
    "/lib/runtime-bridge/session/session-load.ts": "export const sendRuntimeCommand = () => true;",
  },
});

const { activityTs, bucketKey } = await import("../../components/sidebar/sessions/helpers.ts");
const { buildSections } = await import("../../components/sidebar/sessions/sections.ts");

const at = (y, m, d, h = 12) => Math.floor(new Date(y, m - 1, d, h).getTime() / 1000);

describe("bucketKey", () => {
  const now = at(2026, 7, 31, 10);

  test("the same calendar day is today", () => {
    assert.equal(bucketKey(at(2026, 7, 31, 1), now), "today");
  });

  test("yesterday evening is not today even though it is under 24 hours ago", () => {
    assert.equal(bucketKey(at(2026, 7, 30, 20), now), "past7");
    assert.equal(bucketKey(at(2026, 7, 25), now), "past7");
  });

  test("past seven days, chats bucket by month in the current year and by year before it", () => {
    assert.equal(bucketKey(at(2026, 7, 10), now), "m-2026-6");
    assert.equal(bucketKey(at(2026, 5, 1), now), "m-2026-4");
    assert.equal(bucketKey(at(2025, 11, 20), now), "y-2025");
  });

  test("an old conversation that was active today buckets as today", () => {
    const conversation = { created_at: at(2026, 1, 1), updated_at: at(2026, 7, 31, 9) };
    assert.equal(bucketKey(activityTs(conversation), now), "today");
  });
});

describe("activityTs", () => {
  test("prefers updated_at and falls back to created_at, then 0", () => {
    assert.equal(activityTs({ created_at: 5, updated_at: 9 }), 9);
    assert.equal(activityTs({ created_at: 111 }), 111);
    assert.equal(activityTs({}), 0);
  });
});

describe("buildSections", () => {
  const now = new Date(2026, 8, 6, 12).getTime() / 1000;
  const items = [
    { id: "old", created_at: now - 20 * 86400 },
    { id: "today", created_at: now },
    { id: "pin", created_at: now, pinned: true },
  ];
  const options = {
    groupBy: "none",
    sort: "recency",
    nowTs: now,
    locale: "en",
    isWorking: () => false,
    labels: { pinned: "Pinned", today: "Today", past7: "Past 7 days", recents: "Recents", working: "Working", completed: "Completed" },
  };
  const order = (direction) => buildSections(items, { ...options, sortDirection: direction }).flatMap((s) => s.items.map((c) => c.id));

  test("pinned conversations come first and date groups follow the sort direction", () => {
    assert.deepEqual(order("asc"), ["pin", "old", "today"]);
    assert.deepEqual(order("desc"), ["pin", "today", "old"]);
  });
});
