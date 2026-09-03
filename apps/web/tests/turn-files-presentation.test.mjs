import assert from "node:assert/strict";
import test from "node:test";

import {
  allFileWritesFailed,
  fileWriteState,
  initialLegacyTurnFilesLoadState,
  legacyTurnFilesLoadReducer,
  shouldRenderTurnFiles,
} from "../components/chat/messages/turn-files-presentation.ts";

test("plain assistant replies do not create a file-change surface", () => {
  assert.equal(shouldRenderTurnFiles(undefined, undefined), false);
  assert.equal(shouldRenderTurnFiles(undefined, [{ type: "text", text: "done" }]), false);
});

test("persisted summaries and direct file tools create the surface", () => {
  assert.equal(shouldRenderTurnFiles({
    version: 2,
    files: [],
    file_count: 0,
    added: 0,
    removed: 0,
  }), true);
  assert.equal(shouldRenderTurnFiles(undefined, [
    { type: "tool", tool: "apply_patch", is_error: false },
  ]), true);
});

test("failed direct writes remain distinguishable from empty turns", () => {
  assert.equal(allFileWritesFailed([
    { type: "tool", tool: "edit", is_error: true },
  ]), true);
  assert.equal(allFileWritesFailed([
    { type: "tool", tool: "edit", is_error: false },
  ]), false);
  assert.equal(fileWriteState(undefined), "none");
  assert.equal(fileWriteState([
    { type: "tool", tool: "write", is_error: false },
  ]), "attempted");
});

test("legacy load state distinguishes empty success, failure, and retry", () => {
  const empty = legacyTurnFilesLoadReducer(
    initialLegacyTurnFilesLoadState,
    { type: "resolved", ok: true },
  );
  assert.deepEqual(empty, { status: "loaded", attempt: 0 });

  const failed = legacyTurnFilesLoadReducer(
    initialLegacyTurnFilesLoadState,
    { type: "resolved", ok: false },
  );
  assert.deepEqual(failed, { status: "error", attempt: 0 });
  assert.deepEqual(legacyTurnFilesLoadReducer(failed, { type: "retry" }), {
    status: "loading",
    attempt: 1,
  });
});
