// Which status an agent's roster row wears and which helpers are listed.
import assert from "node:assert/strict";
import test from "node:test";
import { rosterCost, rosterLive, rosterNeedsReader, rosterStatus, rosterTotal } from "../../../lib/execution/agent-roster.ts";

const facts = (agent_status, extra = {}) => ({ agent_status, queued: false, usage: null, ...extra });

test("the page's view of a helper wins, and the server adds what only it knows", () => {
  assert.equal(rosterStatus("run"), "running");
  assert.equal(rosterStatus("run", facts("running")), "running");
  assert.equal(rosterStatus("run", facts("stalled")), "stalled");
  assert.equal(rosterStatus("run", facts("running", { queued: true })), "waiting");
  assert.equal(rosterStatus("run", facts("needs approval")), "approval");
  assert.equal(rosterStatus("run", facts("needs input")), "input");
  // The chat already holds the card: no read of the roster is needed.
  assert.equal(rosterStatus("approval", facts("running")), "approval");
  assert.equal(rosterStatus("input"), "input");
  assert.equal(rosterStatus("input", facts("needs approval")), "approval");
  assert.equal(rosterStatus("err", facts("idle")), "failed");
  // Stopped by a limit: cancelled on the page, failed in the roster.
  assert.equal(rosterStatus("rest", facts("failed")), "failed");
  assert.equal(rosterStatus("rest", facts("idle")), "idle");
  // A read older than the page never calls an ended helper running.
  assert.equal(rosterStatus("rest", facts("stalled")), "idle");
});

test("a helper that failed or ended is not going any more", () => {
  assert.equal(rosterLive("failed"), false);
  assert.equal(rosterLive("idle"), false);
  assert.equal(rosterLive("stalled"), true);
  assert.deepEqual(["approval", "input", "stalled"].map(rosterNeedsReader), [true, true, false]);
});

test("a cost with an unknown price is left out, never guessed", () => {
  assert.equal(rosterCost(facts("idle", { usage: { tokens: 10, cost_usd: "0.020000", tool_calls: 1 } })), 0.02);
  assert.equal(rosterCost(facts("idle", { usage: { tokens: 10, cost_usd: null, tool_calls: 1 } })), null);
  assert.equal(rosterCost(null), null);
  assert.ok(Math.abs(rosterTotal([0.02, null, 0.05]) - 0.07) < 1e-9);
  assert.equal(rosterTotal([null, null]), null);
});
