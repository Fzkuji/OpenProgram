import assert from "node:assert/strict";
import test from "node:test";

import {
  decideExecutionUpdateOrder,
  removeExecutionUpdateOrders,
} from "../lib/net/execution-update-order.ts";

function accept(orders, executionId, eventSequence, status = "running") {
  const result = decideExecutionUpdateOrder(
    orders[executionId],
    eventSequence,
    status,
    "session-1",
  );
  if (result.next) orders[executionId] = result.next;
  return result.accepted;
}

test("sequenced execution updates cannot move an execution backward", () => {
  const orders = {};
  assert.equal(accept(orders, "exec-1", 3), true);
  assert.equal(accept(orders, "exec-1", 1), false);
  assert.equal(accept(orders, "exec-1", 3), false);
  assert.equal(accept(orders, "exec-1", 4), true);
  assert.equal(accept(orders, "exec-2", 1), true);
});

test("an active execution retains its sequence after more than 1024 other updates", () => {
  const orders = {};
  assert.equal(accept(orders, "exec-active", 7), true);
  for (let index = 0; index <= 1024; index += 1) {
    assert.equal(accept(orders, `exec-${index}`, 1), true);
  }
  assert.equal(accept(orders, "exec-active", 6), false);
});

test("terminal ordering blocks delayed nonterminal frames until trusted cleanup", () => {
  const orders = {};
  assert.equal(accept(orders, "exec-terminal", 9, "completed"), true);
  assert.equal(accept(orders, "exec-terminal", 8, "running"), false);
  assert.equal(accept(orders, "exec-terminal", 10, "running"), false);
  assert.deepEqual(orders["exec-terminal"], {
    sequence: 9,
    terminal: true,
    sessionId: "session-1",
  });

  const reset = removeExecutionUpdateOrders(orders, ["exec-terminal"]);
  assert.deepEqual(reset, {});
  assert.deepEqual(orders["exec-terminal"], {
    sequence: 9,
    terminal: true,
    sessionId: "session-1",
  });
});
