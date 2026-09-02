import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const client = readFileSync(new URL("../lib/net/execution-client.ts", import.meta.url), "utf8");
const hook = readFileSync(new URL("../lib/use-execution-debugger.ts", import.meta.url), "utf8");

test("revision client uses the single canonical REST route family", () => {
  assert.match(client, /\/api\/execution\/revision\/draft/);
  assert.match(client, /\/api\/execution\/\$\{encodeURIComponent\(input\.execution_id\)\}\/revision\/draft/);
  for (const action of ["replace", "discard", "validate", "approve", "publish"]) {
    const canonical = action === "replace" || action === "discard" ? `revision.draft.${action}` : `revision.${action}`;
    assert.match(client, new RegExp(canonical.replaceAll(".", "\\.")));
  }
  assert.match(client, /input\.action === "revision\.draft\.replace" \? "PUT"/);
  assert.match(client, /input\.action === "revision\.draft\.create"/);
  assert.doesNotMatch(client, /\/api\/revision\/drafts/);
});

test("debugger sends strict revision actions and draft versions", () => {
  assert.match(hook, /revision\.draft\.replace/);
  assert.match(hook, /expected_draft_version/);
  assert.match(hook, /validation_id/);
  assert.match(hook, /approval_id/);
  assert.doesNotMatch(hook, /action: "write"/);
});
