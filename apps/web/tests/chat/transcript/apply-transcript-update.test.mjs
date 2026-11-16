import { test } from "node:test";
import assert from "node:assert/strict";
import { applyLocalChange, applyTextFrame, applyTranscriptChanges, drawnRow, viewHoldsTurn } from "../../../lib/chat/transcript/apply-transcript-update.ts";

const row = (id, fields = {}) => ({ id, role: "assistant", content: id, status: "done", ...fields });
const view = (...rows) => ({ order: rows.map((r) => r.id), rows: Object.fromEntries(rows.map((r) => [r.id, r])) });
const context = (held = [], atLatest = true) => ({ holdsTurn: (id) => held.includes(id), atLatest });
const card = (id, status) => ({ id, attach: { status } });

test("a card lands on its reply and later replaces itself in place", () => {
  const start = view(row("r"));
  const first = applyTranscriptChanges(start, [{ kind: "item_upsert", turnId: "t", rowId: "r", list: "attachCards", item: card("c", "running") }], context());
  const second = applyTranscriptChanges(first, [{ kind: "item_upsert", turnId: "t", rowId: "r", list: "attachCards", item: card("c", "completed") }], context());
  assert.deepEqual(second.rows.r.attachCards, [card("c", "completed")]);
});

test("a change to a row this view does not hold creates nothing", () => {
  const change = { kind: "item_upsert", turnId: "t", rowId: "gone", list: "attachCards", item: card("c", "running") };
  const dropped = applyTranscriptChanges(view(row("r")), [change], context());
  assert.deepEqual(dropped.order, ["r"]);
  assert.deepEqual(dropped.effects, []);
  const held = applyTranscriptChanges(view(row("r")), [change], context(["t"]));
  assert.deepEqual(held.order, ["r"]);
  assert.deepEqual(held.effects, [{ kind: "read_turn", turnId: "t" }]);
});

test("a row upsert in a held turn sits after the row it names, and replaces in place", () => {
  const added = applyTranscriptChanges(view(row("a"), row("b")), [{ kind: "row_upsert", turnId: "t", afterRowId: "a", row: row("x") }], context(["t"]));
  assert.deepEqual(added.order, ["a", "x", "b"]);
  const replaced = applyTranscriptChanges(added, [{ kind: "row_upsert", turnId: "t", afterRowId: null, row: row("x", { content: "new" }) }], context(["t"]));
  assert.deepEqual(replaced.order, ["a", "x", "b"]);
  assert.equal(replaced.rows.x.content, "new");
});

test("a row upsert for a turn out of the window creates nothing", () => {
  const out = applyTranscriptChanges(view(row("a")), [{ kind: "row_upsert", turnId: "t", afterRowId: null, row: row("x") }], context());
  assert.deepEqual(out.order, ["a"]);
});

test("a new turn is drawn before a pending send, and only at the newest window", () => {
  const pending = row("u", { role: "user", status: "pending" });
  const turn = { id: "report", rows: [row("report")] };
  const at = applyTranscriptChanges(view(row("a"), pending), [{ kind: "turn_upsert", turn, afterTurnId: "a" }], context());
  assert.deepEqual(at.order, ["a", "report", "u"]);
  assert.equal(at.addedTurns.length, 1);
  const older = applyTranscriptChanges(view(row("a")), [{ kind: "turn_upsert", turn, afterTurnId: "a" }], context([], false));
  assert.deepEqual(older.order, ["a"]);
  assert.equal(older.turnsPastWindow, 1);
});

test("a window that goes stale says how to read", () => {
  const out = applyTranscriptChanges(view(row("a")), [{ kind: "window_stale", head_id: "x", reason: "branch" }], context());
  assert.deepEqual(out.effects, [{ kind: "read_latest" }]);
  const structure = applyTranscriptChanges(view(row("a")), [{ kind: "window_stale", head_id: "x", reason: "structure" }], context());
  assert.deepEqual(structure.effects, [{ kind: "read_anchor" }]);
});

test("a field a server row never carries survives its upsert", () => {
  const start = view(row("a", { retryStatus: { attempt: 2 } }));
  const out = applyTranscriptChanges(start, [{ kind: "row_upsert", turnId: "a", afterRowId: null, row: row("a", { content: "x" }) }], context(["a"]));
  assert.deepEqual(out.rows.a.retryStatus, { attempt: 2 });
});

test("a reply's credential notices stay while it streams and the stored ones win once it is saved", () => {
  const live = [{ to: "Work", from: "Personal", cause: "rate_limit" }];
  const start = view(row("a", { credentialNotices: live }));
  const streaming = applyTranscriptChanges(start, [{ kind: "row_upsert", turnId: "a", afterRowId: null, row: row("a", { content: "x" }) }], context(["a"]));
  assert.deepEqual(streaming.rows.a.credentialNotices, live);
  const stored = [{ to: "Work", from: "Personal", cause: "quota" }];
  const saved = applyTranscriptChanges(streaming, [{ kind: "row_upsert", turnId: "a", afterRowId: null, row: row("a", { credentialNotices: stored }) }], context(["a"]));
  assert.deepEqual(saved.rows.a.credentialNotices, stored);
});

test("applying the same change twice leaves one card", () => {
  const change = { kind: "item_upsert", turnId: "t", rowId: "r", list: "attachCards", item: card("c", "running") };
  const out = applyTranscriptChanges(view(row("r")), [change, change], context());
  assert.equal(out.rows.r.attachCards.length, 1);
});

test("a sub-agent view, with no window, draws a new turn and updates a turn it has", () => {
  const start = view(row("prompt", { role: "user" }));
  const inContext = (rows) => ({ holdsTurn: viewHoldsTurn(undefined, rows), atLatest: true });
  const turn = { id: "next", rows: [row("next", { role: "user" })] };
  const added = applyTranscriptChanges(start, [{ kind: "turn_upsert", turn, afterTurnId: "prompt" }], inContext(start.rows));
  assert.deepEqual(added.order, ["prompt", "next"]);
  const held = { id: "prompt", rows: [row("prompt", { role: "user", content: "drawn" }), row("late")] };
  const updated = applyTranscriptChanges(added, [{ kind: "turn_upsert", turn: held, afterTurnId: null }], inContext(added.rows));
  assert.deepEqual(updated.order, ["prompt", "next"]);
  assert.equal(updated.rows.prompt.content, "drawn");
});

test("a turn the window has is held even before its rows are drawn", () => {
  assert.equal(viewHoldsTurn({ holdsTurn: (id) => id === "t" }, {})("t"), true);
  assert.equal(viewHoldsTurn({ holdsTurn: () => false }, {})("t"), false);
});

const textFrame = (offset, text, fields = {}) => ({ rowId: "r", itemId: "text:0", itemIndex: 0, offset, type: "text", text, ...fields });

test("text frames extend their block by offset, and the same frame twice changes nothing", () => {
  const first = applyTextFrame(view(row("r", { content: "", blocks: [], status: "streaming" })), textFrame(0, "he"));
  const second = applyTextFrame({ order: ["r"], rows: first.rows }, textFrame(2, "llo"));
  const again = applyTextFrame({ order: ["r"], rows: second.rows }, textFrame(2, "llo"));
  assert.equal(again.rows.r.content, "hello");
  assert.equal(again.rows.r.blocks[0].text, "hello");
  assert.equal(again.gap, false);
});

test("the frame that opens a passage after a tool call puts a blank line in the row's text, once", () => {
  const start = view(row("r", { content: "before", status: "streaming", blocks: [{ type: "text", text: "before" }, { type: "tool", tool_call_id: "t" }] }));
  const opening = textFrame(0, "af", { itemId: "text:1", itemIndex: 2, separator: "\n\n" });
  const first = applyTextFrame(start, opening);
  const again = applyTextFrame({ order: ["r"], rows: first.rows }, opening);
  const more = applyTextFrame({ order: ["r"], rows: again.rows }, textFrame(2, "ter", { itemId: "text:1", itemIndex: 2 }));
  assert.equal(more.rows.r.content, "before\n\nafter");
  assert.deepEqual(more.rows.r.blocks.map((block) => block.text), ["before", undefined, "after"]);
});

test("a frame past the block's end asks for a read and writes nothing", () => {
  const start = view(row("r", { content: "", blocks: [], status: "streaming" }));
  const result = applyTextFrame(start, textFrame(5, "late"));
  assert.equal(result.gap, true);
  assert.equal(result.rows, start.rows);
});

test("a text frame for a row the view does not hold creates nothing", () => {
  const start = view(row("other"));
  const result = applyTextFrame(start, textFrame(0, "x"));
  assert.equal(result.rows, start.rows);
  assert.equal(result.gap, false);
});

test("a call root is matched by path, and a tree with no path replaces the running root of its function", () => {
  const start = view(row("r", { callRoots: [{ path: "p", name: "fn", status: "running" }] }));
  const change = (item) => ({ kind: "item_upsert", turnId: "t", rowId: "r", list: "callRoots", item });
  const byPath = applyTranscriptChanges(start, [change({ path: "p", name: "fn", status: "completed" })], context());
  assert.equal(byPath.rows.r.callRoots.length, 1);
  assert.equal(byPath.rows.r.callRoots[0].status, "completed");
  const byName = applyTranscriptChanges(start, [change({ name: "fn", status: "error" })], context());
  assert.equal(byName.rows.r.callRoots.length, 1);
  const other = applyTranscriptChanges(start, [change({ name: "other", status: "running" })], context());
  assert.equal(other.rows.r.callRoots.length, 2);
});

test("a local row takes the server's id in place and leaves when removed", () => {
  const start = applyLocalChange(view(row("a")), { kind: "local_append", row: row("local") });
  const replaced = applyLocalChange(start, { kind: "local_replace", rowId: "local", row: row("server") });
  assert.deepEqual(replaced.order, ["a", "server"]);
  assert.equal(replaced.rows.local, undefined);
  assert.deepEqual(applyLocalChange(replaced, { kind: "local_remove", rowId: "server" }).order, ["a"]);
});

const blockItem = (index, tool = "t") => ({ kind: "item_upsert", turnId: "t", rowId: "r", list: "blocks", index, item: { type: "tool", tool } });

test("a text frame ahead of the tool before it waits, leaves no hole and lands when the tool does", () => {
  const start = view(row("r", { status: "streaming", content: "", blocks: [{ type: "text", id: "b0", text: "" }] }));
  const first = applyTextFrame(start, textFrame(0, "af", { itemId: "b2", itemIndex: 2 }));
  assert.equal(first.gap, false);
  assert.equal(first.rows.r.blocks.length, 1);
  const second = applyTextFrame(first, textFrame(2, "ter", { itemId: "b2", itemIndex: 2 }));
  const out = applyTranscriptChanges(second, [blockItem(1)], context(["t"]));
  assert.deepEqual(out.effects, []);
  assert.deepEqual(out.rows.r.blocks.map((block) => block.type), ["text", "tool", "text"]);
  assert.equal(out.rows.r.blocks[2].text, "after");
  assert.equal(out.heldFrames, undefined);
});

test("a held frame goes with the terminal row that already covers it", () => {
  const start = view(row("r", { status: "streaming", content: "", blocks: [] }));
  const held = applyTextFrame(start, textFrame(0, "x", { itemIndex: 3 }));
  const out = applyTranscriptChanges(held, [{ kind: "row_upsert", turnId: "t", afterRowId: null, row: row("r", { status: "done", content: "whole" }) }], context(["t"]));
  assert.equal(out.heldFrames, undefined);
  assert.equal(out.rows.r.content, "whole");
});

test("a block goes where its index says", () => {
  const start = view(row("r", { blocks: [{ type: "text", text: "a" }, { type: "text", text: "b" }] }));
  const out = applyTranscriptChanges(start, [blockItem(0)], context(["t"]));
  assert.deepEqual(out.rows.r.blocks.map((block) => block.type), ["tool", "text"]);
  const appended = applyTranscriptChanges(start, [blockItem(2)], context(["t"]));
  assert.equal(appended.rows.r.blocks[2].type, "tool");
});

test("a block past the end asks for a read and never leaves a hole", () => {
  const start = view(row("r", { blocks: [] }));
  const out = applyTranscriptChanges(start, [blockItem(2)], context(["t"]));
  assert.deepEqual(out.rows.r.blocks, []);
  assert.deepEqual(out.effects, [{ kind: "read_turn", turnId: "t" }]);
});

test("a null in a patch takes the field off the row", () => {
  const start = view(row("r", { errorDetail: "boom" }));
  const out = applyTranscriptChanges(start, [{ kind: "row_patch", turnId: "t", rowId: "r", fields: { errorDetail: null, usage: { input: 1 } } }], context());
  assert.equal("errorDetail" in out.rows.r, false);
  assert.deepEqual(out.rows.r.usage, { input: 1 });
});

const overlaid = (fields, ...clear) => applyLocalChange(view(row("r", { status: "running" })), { kind: "overlay_set", rowId: "r", fields, clear });
const patch = (fields) => ({ kind: "row_patch", turnId: "t", rowId: "r", fields });

test("an overlay is drawn over the row and never written into it", () => {
  const stopped = overlaid({ status: "cancelling" }, "until_terminal");
  assert.equal(stopped.rows.r.status, "running");
  assert.equal(drawnRow(stopped, "r").status, "cancelling");
  assert.equal(drawnRow(applyLocalChange(stopped, { kind: "overlay_clear", rowId: "r" }), "r").status, "running");
});

test("a server change lands under an overlay and shows when the overlay goes", () => {
  const stopped = overlaid({ status: "cancelling" }, "until_terminal", { until_command_rejected: "c1" });
  const paused = applyTranscriptChanges(stopped, [patch({ status: "paused" })], context());
  assert.equal(drawnRow(paused, "r").status, "cancelling");
  const rejected = applyLocalChange(paused, { kind: "overlay_command_rejected", commandId: "c1" });
  assert.equal(drawnRow(rejected, "r").status, "paused");
  assert.equal(rejected.overlays, undefined);
});

test("a rejection of another command leaves the overlay", () => {
  const stopped = overlaid({ status: "cancelling" }, { until_command_rejected: "c1" });
  assert.equal(drawnRow(applyLocalChange(stopped, { kind: "overlay_command_rejected", commandId: "c2" }), "r").status, "cancelling");
});

test("the terminal row ends an until_terminal overlay", () => {
  const stopped = overlaid({ status: "cancelling" }, "until_terminal");
  const done = applyTranscriptChanges(stopped, [{ kind: "row_upsert", turnId: "t", afterRowId: null, row: row("r", { status: "cancelled" }) }], context(["t"]));
  assert.equal(drawnRow(done, "r").status, "cancelled");
  assert.equal(done.overlays, undefined);
});

test("a whole row during an overlay leaves an overlay meant to outlive it", () => {
  const moved = overlaid({ siblingIndex: 2 }, "until_read");
  const upsert = { kind: "row_upsert", turnId: "t", afterRowId: null, row: row("r", { status: "running", siblingIndex: 1 }) };
  const out = applyTranscriptChanges(moved, [upsert], context(["t"]));
  assert.equal(drawnRow(out, "r").siblingIndex, 2);
  const read = applyLocalChange(out, { kind: "overlay_read_landed" });
  assert.equal(drawnRow(read, "r").siblingIndex, 1);
});

test("an until_field_matches overlay ends when the server has the same value", () => {
  const pinned = overlaid({ turnFiles: [{ path: "a", reverted: true }] }, "until_field_matches");
  const other = applyTranscriptChanges(pinned, [patch({ turnFiles: [{ path: "a", reverted: false }] })], context());
  assert.ok(other.overlays);
  const same = applyTranscriptChanges(other, [patch({ turnFiles: [{ reverted: true, path: "a" }] })], context());
  assert.equal(same.overlays, undefined);
});

test("an until_row_gone overlay ends with its row", () => {
  const retried = overlaid({ status: "running" }, "until_row_gone");
  const gone = applyLocalChange(retried, { kind: "local_remove", rowId: "r" });
  assert.equal(gone.overlays, undefined);
});

test("the frame that opens a block writes its task and step on it; a later frame does not restamp it", () => {
  const start = view(row("r", { content: "", blocks: [], status: "streaming" }));
  const first = applyTextFrame(start, textFrame(0, "he", { task: "T3", step: "16" }));
  assert.deepEqual([first.rows.r.blocks[0].task, first.rows.r.blocks[0].step], ["T3", "16"]);
  const more = applyTextFrame({ order: ["r"], rows: first.rows }, textFrame(2, "llo", { task: "T3", step: "17" }));
  assert.deepEqual([more.rows.r.blocks[0].text, more.rows.r.blocks[0].step], ["hello", "16"]);
  const plain = applyTextFrame(start, textFrame(0, "x"));
  assert.equal("task" in plain.rows.r.blocks[0], false);
});

test("a tool block's task and step arrive with it and follow a later upsert", () => {
  const start = view(row("r", { status: "streaming", blocks: [] }));
  const change = (item) => ({ kind: "item_upsert", turnId: "t", rowId: "r", list: "blocks", index: 0, item });
  const first = applyTranscriptChanges(start, [change({ id: "tool:c1", type: "tool", tool: "grep", tool_call_id: "c1", task: "T3", step: "14" })], context(["t"]));
  assert.deepEqual([first.rows.r.blocks[0].task, first.rows.r.blocks[0].step], ["T3", "14"]);
  const moved = applyTranscriptChanges(first, [change({ id: "tool:c1", type: "tool", tool: "grep", tool_call_id: "c1", task: "T3", step: "15" })], context(["t"]));
  assert.equal(moved.rows.r.blocks[0].step, "15");
});

test("a turn that follows one the view does not hold is not drawn as the newest", () => {
  // A read already held the writes this update sends late: the turn before
  // the window comes again, after a turn this view does not have.
  const older = { id: "before-47", rows: [row("before-47")] };
  const out = applyTranscriptChanges(view(row("cut")), [{ kind: "turn_upsert", turn: older, afterTurnId: "before-46" }], context(["cut"]));
  assert.deepEqual(out.order, ["cut"]);
  assert.equal(out.addedTurns.length, 0);
  assert.equal(out.turnsPastWindow, 0);
});
