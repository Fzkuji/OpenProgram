import assert from "node:assert/strict";
import test from "node:test";

import {
  decideExecutionUpdateOrder,
  removeExecutionUpdateOrders,
} from "../lib/net/execution-update-order.ts";

function accept(
  orders,
  executionId,
  eventSequence,
  status = "running",
  messageIds = ["msg-user", "msg-assistant"],
) {
  const result = decideExecutionUpdateOrder(
    orders[executionId],
    eventSequence,
    status,
    "session-1",
    messageIds,
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
    messageIds: ["msg-user", "msg-assistant"],
  });

  const reset = removeExecutionUpdateOrders(orders, ["exec-terminal"]);
  assert.deepEqual(reset, {});
  assert.deepEqual(orders["exec-terminal"], {
    sequence: 9,
    terminal: true,
    sessionId: "session-1",
    messageIds: ["msg-user", "msg-assistant"],
  });
});

test("transcript cleanup removes only executions linked to removed messages", () => {
  const orders = {};
  assert.equal(accept(orders, "exec-rewind", 9, "completed", ["msg-rewind"]), true);
  assert.equal(accept(orders, "exec-kept", 11, "completed", ["msg-kept"]), true);

  const afterTruncate = removeExecutionUpdateOrders(orders, ["msg-rewind"]);
  assert.equal(afterTruncate["exec-rewind"], undefined);
  assert.deepEqual(afterTruncate["exec-kept"], {
    sequence: 11,
    terminal: true,
    sessionId: "session-1",
    messageIds: ["msg-kept"],
  });
  assert.equal(
    decideExecutionUpdateOrder(afterTruncate["exec-kept"], 10, "running").accepted,
    false,
  );
});

test("fork and retry children share only their parent's trusted transcript links", () => {
  const orders = {};
  const parentMessages = ["msg-parent-user", "msg-parent-assistant"];
  assert.equal(accept(orders, "exec-parent", 9, "completed", parentMessages), true);
  assert.equal(accept(orders, "exec-fork-child", 4, "completed", parentMessages), true);
  assert.equal(accept(orders, "exec-retry-child", 5, "completed", parentMessages), true);
  assert.equal(accept(orders, "exec-sibling", 8, "completed", ["msg-sibling"]), true);

  const afterParentRemoval = removeExecutionUpdateOrders(orders, ["msg-parent-user"]);
  assert.equal(afterParentRemoval["exec-parent"], undefined);
  assert.equal(afterParentRemoval["exec-fork-child"], undefined);
  assert.equal(afterParentRemoval["exec-retry-child"], undefined);
  assert.equal(
    decideExecutionUpdateOrder(afterParentRemoval["exec-sibling"], 7, "running").accepted,
    false,
  );
});
