import assert from "node:assert/strict";
import test from "node:test";
import { executionNeedsAttention, executionStatusLabel, executionGuidance } from "../components/right-sidebar/debugger-presentation.ts";
const text = en => en;

test("ended provider receipt belongs in history with an explicit incomplete label", () => {
  const old = { status: "reconciliation_required", effect_summary: { provider_response_incomplete: true } };
  assert.equal(executionNeedsAttention(old), false);
  assert.equal(executionStatusLabel(old, text), "Ended · response record incomplete");
  assert.match(executionGuidance(old, text), /ended/);
  assert.equal(executionNeedsAttention({ status: "running", effect_summary: {} }), false);
});

test("paused tasks and unresolved external actions remain actionable", () => {
  for (const status of ["paused", "reconciliation_required"]) {
    assert.equal(executionNeedsAttention({ status, effect_summary: {} }), true);
  }
  assert.equal(executionNeedsAttention({ status: "failed", effect_summary: {} }), false);
});
