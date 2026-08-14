import assert from "node:assert/strict";
import test from "node:test";

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

test("writer status follows the server-stamped outcome and pending count", () => {
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
  assert.equal(writerStatusState({
    last_outcome: "success",
    last_success_at: "2026-08-10T13:00:00+00:00",
    last_failure: failure,
    pending_turns: 3,
  }), "pending");
});

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
