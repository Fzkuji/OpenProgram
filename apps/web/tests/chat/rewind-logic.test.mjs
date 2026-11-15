// Rewind's decisions: when the dialog appears, how refills merge with a
// draft, where markers go and how each cause reads.
import assert from "node:assert/strict";
import { test } from "node:test";

const logic = await import("../../lib/chat/rewind/rewind-logic.ts");
const text = (en) => en;

test("a fast check with nothing to restore rewinds at once", () => {
  const plan = { status: "ready", has_changes: false, skipped: [] };
  assert.deepEqual(logic.planOutcome(plan, 100, text), { kind: "instant" });
  assert.deepEqual(logic.planOutcome(plan, 500, text), { kind: "dialog", preview: { kind: "none" } });
});

test("file changes or skipped files open the dialog", () => {
  const changes = logic.planOutcome({ status: "ready", has_changes: true, summary: { files: 2, additions: 3, deletions: 1 } }, 10, text);
  assert.equal(changes.preview.kind, "files");
  assert.equal(logic.statsLine(changes.preview, text), "2 files · +3 −1");
  const skipped = logic.planOutcome({ status: "ready", has_changes: false, skipped: [{ path: "/w/a.py", action: "skip", reason: "other_session" }] }, 10, text);
  assert.equal(skipped.kind, "dialog");
  assert.match(logic.skippedLine(skipped.preview.skipped, text), /a\.py.*another conversation/);
});

test("a point without saved copies offers the conversation only", () => {
  const outcome = logic.planOutcome({ status: "unavailable", code_unavailable_reason: "evicted" }, 10, text);
  assert.deepEqual(outcome, { kind: "dialog", preview: { kind: "no_code", reason: "evicted" } });
  assert.match(logic.noCodeLine("evicted", text), /cleaned up/);
});

test("a running job and a failed plan are refused in plain words", () => {
  assert.match(logic.planOutcome({ status: "blocked", error_code: "job_running" }, 10, text).message, /background job/);
  assert.deepEqual(logic.planOutcome({ status: "error", error: "node 'abc' is not on the active branch" }, 10, text), { kind: "refuse", message: "Can't rewind to this message." });
  assert.match(logic.planOutcome({ status: "blocked", error_code: "archived" }, 10, text).message, /isn't available/);
});

test("a dry run that could not run opens the dialog with nothing to show", () => {
  assert.deepEqual(logic.planOutcome({ status: "error", transient: true }, 10, text), { kind: "dialog", preview: { kind: "none" } });
});

test("only a clean refusal says nothing changed", () => {
  assert.match(logic.applyErrorText(409, "stale_head", text, "aborted"), /unchanged/);
  assert.match(logic.applyErrorText(0, undefined, text), /may not have finished/);
  assert.match(logic.applyErrorText(500, "boom", text), /may not have finished/);
  assert.match(logic.applyErrorText(409, "automatic rollback could not complete", text, "recovery_required"), /may not have finished/);
  assert.equal(logic.applyOutcomeUnknown(409, "aborted"), false);
});

test("Fork waits for the server and a finished reply", () => {
  assert.equal(logic.forkDisabledCause({ offline: true, running: false, lastPrompt: false }), "offline");
  assert.equal(logic.forkDisabledCause({ offline: false, running: true, lastPrompt: true }), "unfinished");
  assert.equal(logic.forkDisabledCause({ offline: false, running: true, lastPrompt: false }), null);
});

test("the dialog card names attachments and keeps line breaks", () => {
  assert.equal(logic.promptPreview("", ["shot.png"]), "shot.png");
  assert.equal(logic.promptPreview(" a\nb ", ["x.png", "y.pdf"]), "x.png · y.pdf · a\nb");
  assert.equal(logic.promptPreview("z".repeat(700)).length, 601);
});

test("disabled reasons follow their priority", () => {
  assert.equal(logic.disabledCause({ running: true, offline: true, uploading: true }), "responding");
  assert.equal(logic.disabledCause({ running: false, offline: true, uploading: true }), "offline");
  assert.equal(logic.disabledCause({ running: false, offline: false, uploading: true }), "uploading");
  assert.equal(logic.disabledCause({ running: false, offline: false, uploading: false }), null);
});

test("runtime prompts and unsent messages cannot rewind", () => {
  assert.equal(logic.isRewindable({ role: "user", id: "u" }), true);
  assert.equal(logic.isRewindable({ role: "user", id: "u", rowKind: "silent" }), false);
  assert.equal(logic.isRewindable({ role: "user", id: "u", status: "pending" }), false);
  assert.equal(logic.isRewindable({ role: "assistant", id: "a" }), false);
});

test("a refill goes above the draft and repeated rewinds do not stack", () => {
  assert.equal(logic.mergeRefill("old", "", ""), "old");
  assert.equal(logic.mergeRefill("old", "mine", ""), "old\n\nmine");
  assert.equal(logic.mergeRefill("older", "old\n\nmine", "old"), "older\n\nmine");
  assert.equal(logic.removeRefill("old\n\nmine", "old"), "mine");
  assert.equal(logic.removeRefill("mine", "old"), "mine");
  assert.equal(logic.removeRefill("old", "old"), "");
});

test("a refill the user went on typing after is left whole", () => {
  assert.equal(logic.removeRefill("fix the login bug", "fix"), "fix the login bug");
  assert.equal(logic.mergeRefill("new", "fix tests now", "fix"), "new\n\nfix tests now");
});

test("a picker row is one line of names and prose, cut at 80", () => {
  assert.equal(logic.pickerLabel("  a\n b ", ["x.png"]), "x.png · a b");
  assert.equal(logic.pickerLabel("y".repeat(90), []), `${"y".repeat(80)}…`);
});

test("an offscreen rewind anchor is not moved to the latest message", () => {
  const stray = [{ edge_id: "e3", variant: "forward", anchor_head_id: "gone", at: 1 }];
  assert.deepEqual(logic.placeMarks(stray, ["u1"], false).tail.map(m => m.edge_id), []);
  assert.deepEqual(logic.placeMarks([...stray, { edge_id: "p", variant: "pending" }], ["u1"], false).tail.map(m => m.edge_id), ["p"]);
  assert.deepEqual(logic.placeMarks([...stray, { edge_id: "p", variant: "pending" }], ["u1"], true).tail, []);
});

test("a pending mark is last and hidden during a run; others follow their anchor", () => {
  const marks = [
    { edge_id: "e1", variant: "pending" },
    { edge_id: "e2", variant: "back", anchor_head_id: "a1", at: 2 },
    { edge_id: "e3", variant: "forward", anchor_head_id: "gone", at: 1 },
  ];
  const placed = logic.placeMarks(marks, ["u1", "a1"], false);
  assert.deepEqual(placed.tail.map(m => m.edge_id), ["e1"]);
  assert.deepEqual(placed.after.get("a1").map(m => m.edge_id), ["e2"]);
  assert.equal(placed.after.has(null), false);
  assert.deepEqual(logic.placeMarks(marks, ["u1", "a1"], true).tail, []);
});

test("refusals read by cause and a retired mark is silent", () => {
  assert.equal(logic.refusalText("undo", "retired", text), null);
  assert.match(logic.refusalText("redo", "gone", text), /redone/);
  assert.match(logic.refusalText("undo", "unsent", text), /Send a message/);
  assert.match(logic.refusalText("redo", "unsent", text), /Send a message/);
  assert.match(logic.applyErrorText(409, "Files changed since you asked to rewind. Rewind again.", text), /changed since/);
});

test("Fork ends at this message's last reply, retry needs the last prompt", () => {
  const byId = { u1: { role: "user" }, a1: { role: "assistant" }, a2: { role: "assistant" }, u2: { role: "user" } };
  const order = ["u1", "a1", "a2", "u2"];
  assert.equal(logic.forkTipId(order, byId, "u1"), "a2");
  assert.equal(logic.forkTipId(order, byId, "u2"), "u2");
  assert.equal(logic.isLastPrompt(order, byId, "u1"), false);
  assert.equal(logic.isLastPrompt(order, byId, "u2"), true);
});

test('file conflicts offer explicit conversation-only rewind with a reason', () => {
  const outcome = logic.planOutcome({ status: 'blocked', error_code: 'file_conflict', code_available: false, code_unavailable_reason: 'conflict' }, 10, text);
  assert.deepEqual(outcome, { kind: 'dialog', preview: { kind: 'no_code', reason: 'conflict' } });
  assert.match(logic.noCodeLine('conflict', text), /No files will be changed/);
});

test("a rewind that restores files names the branch in its way", () => {
  assert.equal(
    logic.blockingText({ branch_name: "nightly", reason_code: "wait_open" }, text),
    "Branch “nightly” is waiting for an approval. Rewinding files now could undo its work; stop it, then rewind.",
  );
  assert.equal(
    logic.blockingText({}, text),
    "Another branch is running. Rewinding files now could undo its work; stop it, then rewind.",
  );
  assert.match(logic.blockingText({ selected: true }, text), /^A turn on this branch is paused/);
});
