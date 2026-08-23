import assert from "node:assert/strict";
import test from "node:test";

import {
  executionKey,
  shouldHonorRunningTaskClear,
} from "../lib/state/running-task-clear.ts";

test("same execution is honored", () => {
  const current = { msg_id: "b", execution_id: "b_reply" };
  assert.equal(executionKey(current), "b_reply");
  assert.equal(
    shouldHonorRunningTaskClear(current, { execution_id: "b_reply" }),
    true,
  );
  assert.equal(
    shouldHonorRunningTaskClear(current, { msg_id: "b" }),
    true,
  );
});

test("old turn does not idle a newer execution", () => {
  const current = { msg_id: "b", execution_id: "b_reply" };
  assert.equal(
    shouldHonorRunningTaskClear(current, { execution_id: "a_reply", msg_id: "a" }),
    false,
  );
});

test("placeholder survives unscoped or old clear", () => {
  const placeholder = { msg_id: "", started_at: 1 };
  assert.equal(executionKey(placeholder), "");
  assert.equal(shouldHonorRunningTaskClear(placeholder, undefined), false);
  assert.equal(
    shouldHonorRunningTaskClear(placeholder, { execution_id: "a_reply" }),
    false,
  );
});

test("empty slot honors any clear", () => {
  assert.equal(shouldHonorRunningTaskClear(undefined, { execution_id: "a_reply" }), true);
  assert.equal(shouldHonorRunningTaskClear(undefined, undefined), true);
});
