import assert from "node:assert/strict";
import test from "node:test";

import {
  commitmentStatusState,
} from "../components/memory/status.ts";

test("commitment status distinguishes empty, open, and closed sets", () => {
  assert.equal(commitmentStatusState({
    counts: { total: 0, open: 0, done: 0, dismissed: 0 },
    records: [],
  }), "empty");
  assert.equal(commitmentStatusState({
    counts: { total: 2, open: 1, done: 1, dismissed: 0 },
    records: [],
  }), "open");
  assert.equal(commitmentStatusState({
    counts: { total: 1, open: 0, done: 1, dismissed: 0 },
    records: [],
  }), "closed");
});
