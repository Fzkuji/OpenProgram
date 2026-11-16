// What an agent's card says on its one row.
import assert from "node:assert/strict";
import test from "node:test";
import { agentCardLive, agentCardState, costOf, taskWords } from "../../../../lib/chat/transcript/agent-card.ts";

test("a question the agent waits on is the state the row names", () => {
  assert.equal(agentCardState({ status: "running" }, null), "running");
  assert.equal(agentCardState({ status: "running" }, "approval"), "needs_approval");
  assert.equal(agentCardState({ status: "running" }, "input"), "needs_input");
  assert.equal(agentCardState({ status: "queued" }, null), "queued");
  assert.equal(agentCardState({ status: "cancelling" }, "approval"), "cancelling");
  // A question left in the store never reopens a finished agent.
  assert.equal(agentCardState({ status: "completed" }, "approval"), "done");
});

test("an agent that failed or was stopped says so, however it ended", () => {
  assert.equal(agentCardState({ status: "errored" }, null), "failed");
  assert.equal(agentCardState({ status: "cancelled", result_status: "failed", result_reason: "timeout" }, null), "failed");
  assert.equal(agentCardState({ status: "cancelled", result_status: "stopped", result_reason: "user" }, null), "stopped");
  assert.equal(agentCardState({ status: "cancelled" }, null), "stopped");
  assert.equal(agentCardState({ status: "completed", result_status: "completed" }, null), "done");
});

test("a reference nobody ran has no agent state", () => {
  assert.equal(agentCardState({ manual: true }, null), "attached");
  assert.equal(agentCardState({}, null), "none");
  assert.equal(agentCardLive("attached"), false);
  assert.equal(agentCardLive("needs_approval"), true);
  assert.equal(agentCardLive("done"), false);
});

test("the task is the prompt's first line without its receipt", () => {
  assert.equal(taskWords("[task from s1:h1 (main conversation)] tracked\n\n  Map the   auth module\nthen report"), "Map the auth module");
  assert.equal(taskWords(undefined), "");
  assert.equal(taskWords("x".repeat(300)).length, 121);
});

test("a cost is a number only when the server sent one", () => {
  assert.equal(costOf("0.020000"), 0.02);
  assert.equal(costOf(null), null);
  assert.equal(costOf(""), null);
  assert.equal(costOf("n/a"), null);
});
