import test from "node:test";
import assert from "node:assert/strict";

import {
  availableExecutionActions,
  buildExecutionCommand,
  cursorHealth,
  reduceExecutionEvent,
} from "../lib/execution-debugger.ts";

const snapshot = {
  execution_id: "exec-1",
  job_id: "exec-1",
  run_id: "run-1",
  parent_execution_id: null,
  project_id: "project-1",
  session_id: "session-1",
  revision_id: "rev-1",
  status: "paused",
  status_version: 7,
  reason_code: null,
  current_attempt_id: null,
  owner_lease: null,
  resource: { resource_state: "released", queue_wait: null },
  checkpoint_head_id: "cp-7",
  safe_point: { kind: "agent.tool.action.after", step_id: "tool-2", phase: "after" },
  capabilities: {
    pause: true,
    step: true,
    steer: true,
    fork: true,
    retry: false,
    safe_point_kinds: ["agent.tool.action.after"],
    state_schema_version: 1,
  },
  pending_command_ids: [],
  active_child_ids: ["exec-2"],
  effect_summary: { unresolved: 0, committed: 2 },
  terminal_at: null,
  updated_at: 1,
  event_sequence: 7,
};

test("canonical actions derive from status, capability, and checkpoint", () => {
  assert.deepEqual(availableExecutionActions(snapshot), [
    "continue",
    "step",
    "steer",
    "fork",
    "cancel",
  ]);
});

test("commands contain only canonical target and optimistic version", () => {
  const command = buildExecutionCommand(snapshot, "continue", "cmd-1");
  assert.deepEqual(command, {
    type: "execution.command",
    action: "execution.continue",
    command_id: "cmd-1",
    execution_id: "exec-1",
    expected_version: 7,
    payload: {},
  });
});

test("cursor health distinguishes reconnect, gap, stale, and healthy state", () => {
  assert.equal(cursorHealth({ expected: 8, received: 8, connected: true }), "healthy");
  assert.equal(cursorHealth({ expected: 8, received: 9, connected: true }), "gap");
  assert.equal(cursorHealth({ expected: 8, received: 8, connected: false }), "reconnecting");
  assert.equal(cursorHealth({ expected: 8, received: 8, connected: true, snapshotVersion: 7, eventVersion: 6 }), "stale");
});

test("event reducer rejects a gap or stale execution event", () => {
  assert.deepEqual(
    reduceExecutionEvent(snapshot, { sequence: 9, status_version: 8, execution: snapshot }),
    { kind: "gap", expected: 8, received: 9 },
  );
  assert.deepEqual(
    reduceExecutionEvent(snapshot, { sequence: 8, status_version: 6, execution: snapshot }),
    { kind: "stale", snapshotVersion: 7, eventVersion: 6 },
  );
});
