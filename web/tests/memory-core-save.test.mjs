import assert from "node:assert/strict";
import test from "node:test";

import { coreSaveStatus } from "../components/memory/core-save-state.ts";

test("a completed Core save is marked saved only for the submitted text", () => {
  assert.equal(coreSaveStatus("saved text", "saved text", true), "saved");
  assert.equal(coreSaveStatus("new edits", "saved text", true), "");
  assert.equal(coreSaveStatus("saved text", "saved text", false), "error");
});
