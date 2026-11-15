import assert from "node:assert/strict";
import test from "node:test";

import { postRevisionDraftCommand } from "../../lib/net/execution/execution-client.ts";

async function sent(input) {
  const requests = [];
  const previous = globalThis.fetch;
  globalThis.fetch = async (path, init) => {
    requests.push({ path, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    return { ok: false, status: 409, json: async () => ({ error: "stub" }) };
  };
  try {
    await postRevisionDraftCommand(input).catch(() => {});
  } finally {
    globalThis.fetch = previous;
  }
  return requests;
}

test("revision commands use the single canonical REST route family", async () => {
  const [create] = await sent({ execution_id: "e 1", action: "revision.draft.create", payload: { source_checkpoint_id: "c" } });
  assert.deepEqual([create.path, create.method], ["/api/execution/revision/draft", "POST"]);
  assert.equal(create.body.type, "revision.draft");
  assert.equal(create.body.action, "revision.draft.create");
  assert.equal("draft_id" in create.body, false);
  for (const [action, path, method] of [
    ["revision.draft.replace", "replace", "PUT"],
    ["revision.draft.discard", "discard", "POST"],
    ["revision.validate", "validate", "POST"],
    ["revision.approve", "approve", "POST"],
    ["revision.publish", "publish", "POST"],
  ]) {
    const [request] = await sent({ execution_id: "e1", action, draft_id: "d/1", expected_draft_version: 3 });
    assert.deepEqual([request.path, request.method], [`/api/execution/revision/draft/d%2F1/${path}`, method], action);
    assert.equal(request.body.action, action);
    assert.equal(request.body.expected_draft_version, 3);
  }
  const [read] = await sent({ execution_id: "e 1", action: "revision.draft.get", draft_id: "d1" });
  assert.deepEqual([read.path, read.method], ["/api/execution/e%201/revision/draft/d1", "GET"]);
});

test("a draft command without its draft id or version is refused before sending", async () => {
  assert.deepEqual(await sent({ execution_id: "e1", action: "revision.approve", expected_draft_version: 1 }), []);
  assert.deepEqual(await sent({ execution_id: "e1", action: "revision.approve", draft_id: "d1" }), []);
});
