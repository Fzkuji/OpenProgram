import assert from "node:assert/strict";
import test from "node:test";

import { reviewTabId } from "../lib/state/center-tab-ids.ts";
import { openReviewTabLayout } from "../lib/state/review-tab-layout.ts";

test("Review tabs are deterministic per session and source turn", () => {
  assert.equal(reviewTabId("s1", "a1"), "r:s1:a1");
  assert.equal(reviewTabId("s1", "a1"), reviewTabId("s1", "a1"));
  assert.notEqual(reviewTabId("s1", "a1"), reviewTabId("s1", "a2"));
  assert.notEqual(reviewTabId("s1", "a1"), reviewTabId("s2", "a1"));
  assert.equal(reviewTabId("s1"), "r:s1:branch");
});

test("Review opens to the right of an unsplit chat", () => {
  const result = openReviewTabLayout(
    [{ id: "s:s1", kind: "session", title: "Chat", sessionId: "s1" }],
    [],
    "s1",
    "a1",
    "turn",
    "/repo/a.py",
  );
  assert.equal(result.id, "r:s1:a1");
  assert.deepEqual(result.groups[0].memberIds, ["s:s1", "r:s1:a1"]);
  const repeated = openReviewTabLayout(
    result.tabs, result.groups, "s1", "a1", "turn", "/repo/b.py",
  );
  assert.equal(repeated.tabs.filter((tab) => tab.id === "r:s1:a1").length, 1);
  assert.equal(
    repeated.tabs.find((tab) => tab.id === "r:s1:a1").reviewPath,
    "/repo/b.py",
  );
});

test("Review never replaces an existing two-member split", () => {
  const tabs = [
    { id: "s:s1", kind: "session", title: "Chat", sessionId: "s1" },
    { id: "f:p:a.py", kind: "file", title: "a.py", projectId: "p", path: "a.py" },
  ];
  const groups = [{
    id: "g:existing",
    memberIds: ["s:s1", "f:p:a.py"],
    visibleIds: ["s:s1", "f:p:a.py"],
    focusedId: "s:s1",
  }];
  const result = openReviewTabLayout(tabs, groups, "s1", "a1");
  assert.deepEqual(result.groups, groups);
  assert.equal(result.tabs.some((tab) => tab.id === "r:s1:a1"), true);
});
