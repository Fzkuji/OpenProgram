import assert from "node:assert/strict";
import test from "node:test";

const values = new Map();
globalThis.window = {
  sessionStorage: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  },
};

const {
  loadExecutionCursors,
  recordExecutionCursor,
} = await import("../lib/net/execution-cursor.ts");

test("execution cursor persists and requests replay only for a gap", () => {
  assert.deepEqual(recordExecutionCursor({
    execution_id: "exec-1", next_sequence: 2, snapshot_status_version: 1,
  }), {
    cursor: { execution_id: "exec-1", next_sequence: 2, snapshot_status_version: 1 },
  });
  assert.equal(recordExecutionCursor({
    execution_id: "exec-1", next_sequence: 5, snapshot_status_version: 4,
  }).replayAfter, 1);
  assert.deepEqual(loadExecutionCursors(), [{
    execution_id: "exec-1", next_sequence: 5, snapshot_status_version: 4,
  }]);
});
