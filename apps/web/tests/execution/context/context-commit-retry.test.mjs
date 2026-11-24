import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { detailReplyOutcome, retryDelay } from "../../../components/right-sidebar/context-commit-timeline/retry.ts";

const timeline = readFileSync(new URL("../../../components/right-sidebar/context-commit-timeline/index.tsx", import.meta.url), "utf8");

test("the Context tab waits 1 s, doubling, at most 30 s between asks", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 10].map(retryDelay), [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  assert.doesNotMatch(timeline, /setInterval|RETRY_MS/);
});

test("a commit the server has no items for is not asked for again", () => {
  assert.equal(detailReplyOutcome({ items: [] }), "kept");
  assert.equal(detailReplyOutcome({ error: "commit 'x' not found", items: [] }), "refused");
  assert.equal(detailReplyOutcome({ error: null }), "refused");
  // A refused commit stops the open row's asking and is not asked on reopen.
  assert.match(timeline, /if \(!expanded \|\| expandedAnswered\) return;/);
  assert.match(timeline, /!details\[id\] && !refused\.has\(id\)/);
});
