// A worker restart cuts a reply off; the resume turn (prompt stamped
// trigger "restart") continues it, and the chat draws both as one reply.
import assert from "node:assert/strict";
import { test } from "node:test";

import { continuedRows, joinedReply } from "../../../lib/chat/transcript/restart-continuation.ts";

// Rows as the server projects them: the continuing reply names the cut one
// (transcript/rows.py, tested in tests/unit/webui/test_transcript_rows.py).
function restartedTranscript() {
  const msgs = [
    { id: "u1", role: "user", rowKind: "user", content: "run sleep 40" },
    { id: "u1_reply", role: "assistant", rowKind: "assistant", content: "", continuedBy: "r_user_reply",
      blocks: [{ type: "tool", tool: "bash", tool_call_id: "t1", is_error: true }] },
    { id: "r_user", role: "user", rowKind: "silent", content: "Continue the interrupted task", trigger: "restart" },
    { id: "r_user_reply", role: "assistant", rowKind: "assistant", content: "done", continues: ["u1_reply"],
      blocks: [{ type: "tool", tool: "bash", tool_call_id: "t2" }, { type: "text", text: "done" }] },
  ];
  return Object.fromEntries(msgs.map((m) => [m.id, m]));
}

test("the continuing reply opens with the cut-off steps and a restart step where it fell", () => {
  const byId = restartedTranscript();
  const cutOff = continuedRows(byId, byId.r_user_reply);
  assert.deepEqual(cutOff.map((m) => m.id), ["u1_reply"]);
  assert.deepEqual(
    joinedReply(cutOff, byId.r_user_reply, (en) => en).blocks.map((b) => b.type),
    ["tool", "restart", "tool", "text"],
  );
});

test("a reply nothing cut off continues nothing", () => {
  const byId = restartedTranscript();
  assert.deepEqual(continuedRows(byId, byId.u1_reply), []);
});

test("a continued row this view does not hold is left out, not invented", () => {
  assert.deepEqual(continuedRows({}, { id: "r", role: "assistant", rowKind: "assistant", content: "", continues: ["gone"] }), []);
});

const part = (id, fields) => ({ id, role: "assistant", content: "", status: "done", ...fields });
const files = (rows, added = 1, removed = 0) => ({ version: 1, files: rows.map((path) => ({ path, op: "edit", added, removed })), file_count: rows.length, added: added * rows.length, removed: removed * rows.length });

test("copy covers the whole joined reply, in order", () => {
  const joined = joinedReply([part("a", { content: "before the restart" })], part("b", { content: "after it" }), (en) => en);
  assert.equal(joined.content, "before the restart\n\nafter it");
});

test("a part with no text adds no empty paragraph", () => {
  assert.equal(joinedReply([part("a")], part("b", { content: "only this" }), (en) => en).content, "only this");
});

test("file markers from a cut-off part reach the joined reply", () => {
  const marker = "[attachment: a.txt @ /tmp/a.txt]";
  assert.ok(joinedReply([part("a", { content: marker })], part("b", { content: "done" }), (en) => en).content.includes(marker));
});

test("turn files cover every part, a file changed twice counted once with both changes", () => {
  const summary = joinedReply(
    [part("a", { turnFiles: files(["x.py", "y.py"], 2, 1) })],
    part("b", { turnFiles: files(["y.py", "z.py"], 3, 0) }),
    (en) => en,
  ).turnFiles;
  assert.deepEqual(summary.files.map((file) => [file.path, file.added, file.removed]), [["x.py", 2, 1], ["y.py", 5, 1], ["z.py", 3, 0]]);
  assert.equal(summary.file_count, 3);
  assert.equal(summary.added, 10);
  assert.equal(summary.removed, 2);
});

test("turn files from only the cut-off part are kept, and an unknown count stays unknown", () => {
  const only = files(["x.py"]);
  assert.deepEqual(joinedReply([part("a", { turnFiles: only })], part("b"), (en) => en).turnFiles, { ...only, spansTurns: true });
  const unknown = { ...files(["x.py"]), added: null };
  assert.equal(joinedReply([part("a", { turnFiles: unknown })], part("b", { turnFiles: files(["y.py"]) }), (en) => en).turnFiles.added, null);
});

test("a joined list is marked as spanning turns unless every file is the last part's", () => {
  const own = files(["y.py"]);
  assert.equal(joinedReply([part("a")], part("b", { turnFiles: own }), (en) => en).turnFiles, own, "the last part alone is still one turn");
  assert.equal(joinedReply([part("a", { turnFiles: files(["x.py"]) })], part("b", { turnFiles: own }), (en) => en).turnFiles.spansTurns, true);
});

test("a bounded part keeps the joined list counted as incomplete", () => {
  const bounded = { ...files(["x.py"]), file_count: 4 };
  const summary = joinedReply([part("a", { turnFiles: bounded })], part("b", { turnFiles: files(["y.py"]) }), (en) => en).turnFiles;
  assert.equal(summary.files.length, 2);
  assert.equal(summary.file_count, 5);
});

test("a joined list reads as reverted only when every part with files was", () => {
  const reverted = (a, b) => joinedReply(
    [part("a", { turnFiles: files(["x.py"]), reverted: a }), part("none")],
    part("b", { turnFiles: files(["y.py"]), reverted: b }),
    (en) => en,
  ).reverted;
  assert.equal(reverted(false, true), false);
  assert.equal(reverted(true, true), true);
});

test("a failed continuation's error text stays its own", () => {
  const joined = joinedReply([part("a", { content: "earlier" })], part("b", { status: "error", content: "boom" }), (en) => en);
  assert.equal(joined.errorDetail, "boom");
});

test("a reply nothing cut off is returned as it is", () => {
  const reply = part("b", { content: "x" });
  assert.equal(joinedReply([], reply, (en) => en), reply);
});
