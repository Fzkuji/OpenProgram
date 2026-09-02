import assert from "node:assert/strict";
import test from "node:test";

import {
  acceptExecutionUpdate,
  resetExecutionUpdateOrderForTests,
} from "../lib/net/execution-update-order.ts";

test("sequenced execution updates cannot move an execution backward", () => {
  resetExecutionUpdateOrderForTests();
  assert.equal(acceptExecutionUpdate("exec-1", 3), true);
  assert.equal(acceptExecutionUpdate("exec-1", 1), false);
  assert.equal(acceptExecutionUpdate("exec-1", 3), false);
  assert.equal(acceptExecutionUpdate("exec-1", 4), true);
  assert.equal(acceptExecutionUpdate("exec-2", 1), true);
});
