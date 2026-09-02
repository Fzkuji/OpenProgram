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

test("an active execution retains its sequence after more than 1024 other updates", () => {
  resetExecutionUpdateOrderForTests();
  assert.equal(acceptExecutionUpdate("exec-active", 7), true);
  for (let index = 0; index <= 1024; index += 1) {
    assert.equal(acceptExecutionUpdate(`exec-${index}`, 1), true);
  }
  assert.equal(acceptExecutionUpdate("exec-active", 6), false);
});
