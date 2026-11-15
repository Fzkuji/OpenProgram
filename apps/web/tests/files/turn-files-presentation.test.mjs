import assert from "node:assert/strict";
import test from "node:test";

import {
  allFileWritesFailed,
  fileWriteState,
  initialLegacyTurnFilesLoadState,
  legacyTurnFilesLoadReducer,
  shouldRenderTurnFiles,
  turnFilesListLayout,
  turnFilesSummaryComplete,
} from "../../components/chat/messages/turn-files/turn-files-presentation.ts";

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

test("a header count larger than the loaded rows always leaves a visible control", () => {
  // Regression: "12 files changed" with only 3 rows and no toggle.
  assert.deepEqual(turnFilesListLayout(3, 12, false), {
    total: 12, shown: 3, more: 0, collapse: false, overflow: 9,
  });
  assert.deepEqual(turnFilesListLayout(12, 12, false), {
    total: 12, shown: 3, more: 9, collapse: false, overflow: 0,
  });
  assert.deepEqual(turnFilesListLayout(12, 12, true), {
    total: 12, shown: 12, more: 0, collapse: true, overflow: 0,
  });
  // More files than the card holds: the expanded list ends in an overflow row.
  assert.deepEqual(turnFilesListLayout(25, 30, false), {
    total: 30, shown: 3, more: 17, collapse: false, overflow: 0,
  });
  assert.deepEqual(turnFilesListLayout(25, 30, true), {
    total: 30, shown: 20, more: 0, collapse: true, overflow: 10,
  });
  // The header never under-counts the rows it lists.
  assert.equal(turnFilesListLayout(4, 2, false).total, 4);
  assert.deepEqual(turnFilesListLayout(2, 2, false), {
    total: 2, shown: 2, more: 0, collapse: false, overflow: 0,
  });
});

test("bounded embedded summaries are detected as incomplete", () => {
  assert.equal(turnFilesSummaryComplete(3, 12), false);
  assert.equal(turnFilesSummaryComplete(3, 3), true);
  assert.equal(turnFilesSummaryComplete(20, 40), true);
  assert.equal(turnFilesSummaryComplete(0, 0), true);
});

test("history presentation explains a withheld action by reason, never by engine text", async () => {
  const { historyPresentation } = await import("../../components/chat/messages/turn-files/turn-files-history-state.ts");
  const notices = { files_changed: "changed", fallback: "fallback" };
  assert.deepEqual(historyPresentation(null, "", notices), { notice: "", operation: null });
  assert.deepEqual(
    historyPresentation({ status: "ready", operation: "undo" }, "", notices),
    { notice: "", operation: "undo" },
  );
  assert.deepEqual(
    historyPresentation(
      { status: "blocked", operation: null, reason: "files_changed", error: "current file state does not match the recorded source" },
      "",
      notices,
    ),
    { notice: "changed", operation: null },
  );
  assert.deepEqual(
    historyPresentation({ status: "unavailable", operation: null, reason: "later_code", error: "engine text" }, "", notices),
    { notice: "fallback", operation: null },
    "an unknown reason falls back to the generic notice",
  );
  assert.deepEqual(
    historyPresentation({ status: "ready", operation: "undo" }, "STALE_SNAPSHOT", notices),
    { notice: "STALE_SNAPSHOT", operation: null },
    "a failed action reports its own error and withholds the action",
  );
});
