import assert from "node:assert/strict";
import test from "node:test";

import { selectDebuggerInspection } from "../lib/execution-debugger.ts";

test("debugger inspection projection keeps only the selected execution records", () => {
  const selected = selectDebuggerInspection({
    checkpoints: [
      { execution_id: "exec-a", checkpoint_id: "cp-a" },
      { execution_id: "exec-b", checkpoint_id: "cp-b" },
    ],
    waits: [
      { execution_id: "exec-a", wait_id: "wait-a" },
      { execution_id: "exec-b", wait_id: "wait-b" },
    ],
    drafts: [
      { source_execution_id: "exec-a", draft_id: "draft-a" },
      { source_execution_id: "exec-b", draft_id: "draft-b" },
    ],
  }, "exec-a");

  assert.deepEqual(selected.checkpoints.map((item) => item.checkpoint_id), ["cp-a"]);
  assert.deepEqual(selected.waits.map((item) => item.wait_id), ["wait-a"]);
  assert.deepEqual(selected.drafts.map((item) => item.draft_id), ["draft-a"]);
});
