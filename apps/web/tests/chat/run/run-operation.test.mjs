// A branch's running operation, read from its running task: what the run
// indicator says and what a Stop sends.
import assert from "node:assert/strict";
import { test } from "node:test";

const op = await import("../../../lib/chat/run/run-operation.ts");
const text = (en) => en;

test("/compact and a turn's own compaction both read as compacting", () => {
  assert.equal(op.isCompacting({ msg_id: "c", op_kind: "compact", op_id: "op_1" }), true);
  assert.equal(op.isCompacting({ msg_id: "m", execution_id: "e", phase: "compact" }), true);
  assert.equal(op.isCompacting({ msg_id: "m", execution_id: "e", op_kind: "reply" }), false);
  assert.equal(op.operationStatusText({ msg_id: "c", op_kind: "compact" }, text), "Compacting context…");
  assert.equal(op.operationStatusText({ msg_id: "r", op_kind: "rewind" }, text), "Rewinding…");
  assert.equal(op.operationStatusText({ msg_id: "m", op_kind: "retry", execution_id: "e" }, text), null);
});

test("Stop names the operation only when it has no execution", () => {
  assert.deepEqual(op.operationCancelCommand("s", { msg_id: "c", op_kind: "compact", op_id: "op_1" }),
    { action: "operation.cancel", session_id: "s", op_id: "op_1" });
  // A retry that became a turn is stopped through its execution.
  assert.equal(op.operationCancelCommand("s", { msg_id: "m", op_kind: "retry", op_id: "op_2", execution_id: "e" }), null);
  assert.equal(op.operationCancelCommand("s", null), null);
});
