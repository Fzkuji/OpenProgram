import assert from "node:assert/strict";

import { writerStatusState } from "../components/memory/status.ts";

const empty = {
  last_success_at: null,
  last_failure: null,
  pending_turns: 0,
};
assert.equal(writerStatusState(empty), "empty");
assert.equal(writerStatusState({ ...empty, pending_turns: 2 }), "pending");
assert.equal(writerStatusState({ ...empty, pending_turns: null }), "unavailable");
assert.equal(writerStatusState({
  last_success_at: null,
  last_failure: {
    at: "2026-08-10T12:00:00+00:00",
    reason: "ProviderUnavailable",
    retryable: true,
  },
  pending_turns: 2,
}), "failed");
assert.equal(writerStatusState({
  last_success_at: "2026-08-10T13:00:00+00:00",
  last_failure: {
    at: "2026-08-10T12:00:00+00:00",
    reason: "ProviderUnavailable",
    retryable: true,
  },
  pending_turns: 0,
}), "idle");

console.log("memory writer status checks passed");
