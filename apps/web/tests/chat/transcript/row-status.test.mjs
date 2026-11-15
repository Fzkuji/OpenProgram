// The one table of what a row's status means, over every value the wire can carry.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  AGENT_STATUSES, ROW_STATUSES, isAgentFinished, isAgentLive, isAgentRunning, isBeingStopped,
  isLive, isOpen, isSettled, isStopped, rowStatusForExecution, storedRowStatus,
} from "../../../lib/chat/transcript/row-status.ts";

const ROWS = {
  pending: [true, true, false], running: [true, true, false], streaming: [true, true, false],
  cancelling: [true, true, false], paused: [false, true, false], done: [false, false, true],
  completed: [false, false, true], error: [false, false, true], cancelled: [false, false, true],
  interrupted: [false, false, true],
};

test("every row status on the wire has one meaning", () => {
  assert.deepEqual([...ROW_STATUSES].sort(), Object.keys(ROWS).sort());
  for (const [status, expected] of Object.entries(ROWS)) {
    assert.deepEqual([isLive(status), isOpen(status), isSettled(status)], expected, status);
  }
});

test("a row with no status or an unknown one is neither open nor settled", () => {
  for (const status of [undefined, null, "", "constructor", "no-such-status"]) {
    assert.deepEqual([isLive(status), isOpen(status), isSettled(status)], [false, false, false], String(status));
  }
});

test("only a stopped or cut-off row counts as stopped", () => {
  assert.deepEqual(ROW_STATUSES.filter(isStopped).sort(), ["cancelled", "interrupted"]);
});

test("a row counts as being stopped from the moment Stop lands", () => {
  assert.deepEqual(ROW_STATUSES.filter(isBeingStopped).sort(), ["cancelled", "cancelling"]);
});

test("a reload shows each stored status, cancelling as cancelled and none by its row type", () => {
  for (const status of ["running", "streaming", "paused", "error", "cancelled", "interrupted"]) {
    assert.equal(storedRowStatus(status), status);
  }
  assert.equal(storedRowStatus("cancelling"), "cancelled");
  assert.equal(storedRowStatus("done"), "done");
  assert.equal(storedRowStatus("completed"), "done");
  assert.equal(storedRowStatus(undefined), "done");
  assert.equal(storedRowStatus(undefined, "error"), "error");
});

test("a row takes its execution's status through one mapping", () => {
  const expected = {
    completed: "done", done: "done", failed: "error", error: "error", reconciliation_required: "error",
    cancelled: "cancelled", interrupted: "interrupted", cancelling: "cancelling", paused: "paused",
    running: "running", queued: "running", unknown_value: "running",
  };
  for (const [status, row] of Object.entries(expected)) assert.equal(rowStatusForExecution(status), row, status);
});

test("every agent status has one meaning, and cancelling is live but not running", () => {
  const expected = {
    pending: [true, true, false], queued: [true, true, false], running: [true, true, false],
    cancelling: [false, true, false], completed: [false, false, true], errored: [false, false, true],
    failed: [false, false, true], cancelled: [false, false, true],
  };
  assert.deepEqual([...AGENT_STATUSES].sort(), Object.keys(expected).sort());
  for (const [status, row] of Object.entries(expected)) {
    assert.deepEqual([isAgentRunning(status), isAgentLive(status), isAgentFinished(status)], row, status);
  }
});
