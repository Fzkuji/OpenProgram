import assert from "node:assert/strict";
import test from "node:test";
import { runtimeSummaryLabel } from "../components/chat/messages/runtime-summary.ts";

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
