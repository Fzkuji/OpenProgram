import assert from "node:assert/strict";
import test from "node:test";
import { runtimeAnswer, runtimeSummaryLabel } from "../../components/chat/messages/runtime/runtime-summary.ts";

test("system access wait is displayed as paused without elapsed runtime", () => {
  const label = runtimeSummaryLabel({
    fnName: "gui_agent",
    status: "paused",
    timestamp: Date.now() - 120_000,
    tree: {
      status: "running",
      output: JSON.stringify({
        status: "infeasible",
        reason_code: "system_access_required",
        system_access: [{id: "screen_recording", status: "not_granted"}],
      },),
    },
  });
  assert.match(label, /^gui_agent · Paused · 1 step$/);
  assert.doesNotMatch(label, /Waiting|Running|02:00/);
});

// Rows as the server projects a stored runtime placeholder (transcript/rows.py).
test("persisted paused gui wait does not become Error/null", () => {
  const msg = {
    id: "b2d52ad91d86",
    role: "assistant",
    rowKind: "function_run",
    display: "runtime",
    function: "gui_agent",
    status: "paused",
    contextTree: {
      path: "b2d52ad91d86",
      name: "gui_agent",
      status: "paused",
      output: "null",
    },
  };
  const label = runtimeSummaryLabel({
    fnName: msg.function || "",
    status: msg.status,
    tree: msg.contextTree,
  });
  assert.equal(label, "gui_agent · Paused · 1 step");
  assert.doesNotMatch(label, /Error|null/);
  assert.equal(runtimeAnswer({
    fnName: "gui_agent",
    status: msg.status,
    tree: msg.contextTree,
  }), null);
});

test("failed and cancelled results stay visible while paused null stays hidden", () => {
  const tree = {
    path: "terminal-node",
    name: "gui_agent",
    status: "error",
    output: "null",
  };
  const failed = { id: "failed-node", role: "assistant", rowKind: "function_run", function: "gui_agent", status: "error", contextTree: tree };
  const cancelled = {
    id: "cancelled-node", role: "assistant", rowKind: "function_run", function: "gui_agent",
    status: "cancelled", contextTree: { ...tree, status: "cancelled" },
  };
  const failedLabel = runtimeSummaryLabel({
    fnName: "gui_agent", status: failed.status, tree: failed.contextTree,
  });
  assert.match(failedLabel, /Error/);
  assert.doesNotMatch(failedLabel, /null/);
  assert.match(runtimeSummaryLabel({
    fnName: "gui_agent", status: cancelled.status, tree: cancelled.contextTree,
  }), /Cancelled/);
  assert.equal(runtimeAnswer({
    fnName: "gui_agent", status: "error",
    tree: { ...tree, output: "captured failure details" },
  }), "captured failure details");
  assert.equal(runtimeAnswer({
    fnName: "gui_agent", status: "cancelled",
    tree: { ...tree, output: "cancelled after user request" },
  }), "cancelled after user request");
});


test("cancelled GUI wait omits serialized null while retaining actual output", () => {
  assert.equal(runtimeAnswer({fnName: "gui_agent", status: "cancelled", tree: {output: "null"}}), null);
  assert.equal(runtimeAnswer({fnName: "gui_agent", status: "cancelled", tree: {output: "partial output"}}), "partial output");
});
