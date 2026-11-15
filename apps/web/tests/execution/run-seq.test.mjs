import assert from "node:assert/strict";
import test from "node:test";

import { acceptRunFrame, resetRunSeqForTests } from "../../lib/chat/decisions/run-seq.ts";

test("a session load read before the turn's clear cannot revive the turn", () => {
  resetRunSeqForTests();
  // The load read "running" at seq 10, the clear was broadcast at seq 11,
  // and the load's frame arrives last.
  assert.equal(acceptRunFrame("s1", 11), true);
  assert.equal(acceptRunFrame("s1", 10), false);
});

test("a stale clear cannot idle a newer turn", () => {
  resetRunSeqForTests();
  assert.equal(acceptRunFrame("s1", 20), true);
  assert.equal(acceptRunFrame("s1", 19), false);
  assert.equal(acceptRunFrame("s1", 20), false);
  assert.equal(acceptRunFrame("s1", 21), true);
});

test("sessions are ordered independently", () => {
  resetRunSeqForTests();
  assert.equal(acceptRunFrame("a", 50), true);
  assert.equal(acceptRunFrame("b", 5), true);
});

test("frames without a run_seq are applied", () => {
  resetRunSeqForTests();
  assert.equal(acceptRunFrame("s1", 30), true);
  assert.equal(acceptRunFrame("s1", undefined), true);
  assert.equal(acceptRunFrame(undefined, 1), true);
});
