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
} = await import("../../lib/net/execution/execution-cursor.ts");

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
    execution_id: "exec-1", next_sequence: 2, snapshot_status_version: 1,
  }]);
});

test('delayed cursors cannot regress persisted recovery position', () => {
  recordExecutionCursor({execution_id:'monotonic',next_sequence:9,snapshot_status_version:5});
  recordExecutionCursor({execution_id:'monotonic',next_sequence:3,snapshot_status_version:2});
  assert.equal(loadExecutionCursors().find(c=>c.execution_id==='monotonic').next_sequence,9);
});
test('terminal executions no longer participate in reconnect recovery', () => {
  recordExecutionCursor({execution_id:'finished',next_sequence:9,snapshot_status_version:5},{status:'completed',session_id:'s'});
  assert.equal(loadExecutionCursors().some(c=>c.execution_id==='finished'),false);
});
test('unavailable browser storage cannot abort live event delivery', () => {
  const saved=window.sessionStorage.setItem;
  window.sessionStorage.setItem=()=>{throw Error('quota')};
  try {assert.doesNotThrow(()=>recordExecutionCursor({execution_id:'quota',next_sequence:2,snapshot_status_version:1}));}
  finally {window.sessionStorage.setItem=saved;}
});
