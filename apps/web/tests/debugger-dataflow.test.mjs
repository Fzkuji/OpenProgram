import assert from "node:assert/strict";
import test from "node:test";

import { selectDebuggerInspection } from "../lib/execution-debugger.ts";
import { buildWaitAnswer } from "../lib/execution-wait.ts";

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

test("wait answers preserve the canonical typed payload for each wait kind", () => {
  assert.deepEqual(buildWaitAnswer({
    kind: "approval",
    policy_snapshot: { allowed_scopes: ["once", "always"] },
  }, undefined, "always"), { answer: "approve", scope: "always" });

  assert.deepEqual(buildWaitAnswer({
    kind: "form",
    request: { schema: {
      retries: { type: "integer", minimum: 0 },
      dry_run: { type: "boolean" },
      profile: { type: "string", enum: ["fast", "safe"] },
    } },
  }, { retries: 2, dry_run: true, profile: "safe" }), {
    retries: 2, dry_run: true, profile: "safe",
  });

  assert.deepEqual(buildWaitAnswer({
    kind: "ask_many",
    request: { questions: [
      { prompt: "one", options: ["a"], multi: false, allow_custom: false },
      { prompt: "two", options: ["b"], multi: true, allow_custom: false },
    ] },
  }, ["a", ["b"]]), ["a", ["b"]]);

  assert.equal(buildWaitAnswer({ kind: "confirm", request: { multi: false } }, "yes"), "yes");
  assert.deepEqual(buildWaitAnswer({ kind: "ask", request: { multi: true } }, ["a", "b"]), ["a", "b"]);
});

test("approval and structured waits reject payloads outside their declared schema", () => {
  assert.throws(() => buildWaitAnswer({ kind: "approval", policy_snapshot: { allowed_scopes: ["once"] } }, undefined, "always"));
  assert.throws(() => buildWaitAnswer({ kind: "form", request: { schema: { retries: { type: "integer" } } } }, { retries: 1.5 }));
  assert.throws(() => buildWaitAnswer({ kind: "ask_many", request: { questions: [{ prompt: "one", options: [], multi: false, allow_custom: true }] } }, []));
});
