import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  commitmentStatusState,
  writerStatusState,
} from "../components/memory/status.ts";

const failure = {
  at: "2026-08-10T12:00:00+00:00",
  reason_code: "MODEL_TRANSPORT",
  retryable: true,
};
const empty = {
  last_outcome: null,
  last_success_at: null,
  last_failure: null,
  pending_turns: 0,
};
assert.equal(writerStatusState(empty), "unrecorded");
assert.equal(writerStatusState({ ...empty, pending_turns: 2 }), "pending");
assert.equal(
  writerStatusState({ ...empty, pending_turns: null }),
  "pending_count_unavailable",
);
assert.equal(writerStatusState({
  last_outcome: "failure",
  last_success_at: null,
  last_failure: failure,
  pending_turns: 2,
}), "failed");
assert.equal(writerStatusState({
  last_outcome: "success",
  last_success_at: "2026-08-10T13:00:00+00:00",
  last_failure: failure,
  pending_turns: 0,
}), "up_to_date");
// Same-millisecond stamps: only `last_outcome` separates these two.
assert.equal(writerStatusState({
  last_outcome: "failure",
  last_success_at: "2026-08-10T12:00:00+00:00",
  last_failure: { ...failure, at: "2026-08-10T12:00:00+00:00" },
  pending_turns: 0,
}), "failed");
assert.equal(writerStatusState({
  last_outcome: "success",
  last_success_at: "2026-08-10T12:00:00+00:00",
  last_failure: { ...failure, at: "2026-08-10T12:00:00+00:00" },
  pending_turns: 0,
}), "up_to_date");
// A recorded failure with a pending count still reports the pending work
// once a later success cleared the failure as the latest outcome.
assert.equal(writerStatusState({
  last_outcome: "success",
  last_success_at: "2026-08-10T13:00:00+00:00",
  last_failure: failure,
  pending_turns: 3,
}), "pending");

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

const memoryPage = readFileSync(
  new URL("../components/memory/index.tsx", import.meta.url),
  "utf8",
);
assert.match(memoryPage, /\/api\/memory\/commitments\/transition/);
assert.match(memoryPage, /"done"/);
assert.match(memoryPage, /"dismissed"/);

console.log("memory writer status checks passed");
