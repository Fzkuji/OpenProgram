/** Numbered updates through the production socket hook and stores. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadRecordings, withOpenChat } from "../../_support/transcript-replay.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const recordings = Object.fromEntries(loadRecordings(join(here, "recordings")).map((recording) => [recording.name, recording]));
const stored = recordings.sync_sub_agent;
const session = stored.session_id;
const loaded = stored.cold.find((frame) => frame.type === "session_loaded");
const lastTurn = loaded.data.turns.at(-1);
const replyId = lastTurn.rows.findLast((row) => row.role === "assistant").id;
const cursor = loaded.data.window.revision;
const update = (previous, revision, changes) => ({ type: "transcript_update", data: { session_id: session, view: session, previous, revision, changes } });
const card = (id) => ({ kind: "item_upsert", turnId: lastTurn.id, rowId: replyId, list: "attachCards", item: { id, role: "assistant", function: "attach", content: "", attach: { status: "running" } } });
const reads = (sent) => sent().filter((message) => message.action === "load_session").length;
const pageReply = [{ type: "session_history_page", data: { id: session, turns: loaded.data.turns, window: { ...loaded.data.window, revision: cursor + 50 }, request_id: null, action: "load_session" } }];
const olderFrames = stored.cold.map((frame) => frame === loaded
  ? { ...frame, data: { ...frame.data, window: { ...frame.data.window, after: "newer-turn", end: frame.data.window.end, total: frame.data.window.total + 50 } } }
  : frame);

test("a reader in older history keeps its window through 100 background updates", async () => {
  await withOpenChat(olderFrames, async ({ deliver, ids, row, sent }) => {
    const before = ids();
    const asked = reads(sent);
    let previous = cursor;
    for (let number = 1; number <= 100; number += 1) {
      const revision = previous + 1;
      await deliver(update(previous, revision, [{ kind: "turn_upsert", afterTurnId: "elsewhere", turn: { id: `t${number}`, rows: [{ id: `t${number}`, role: "assistant", content: "x" }] } }]));
      previous = revision;
    }
    assert.deepEqual(ids(), before);
    assert.equal(reads(sent), asked, "no read was asked for");
    assert.ok(row(replyId));
  });
});

test("updates that do not follow the cursor cost one read, and it replaces the window", async () => {
  await withOpenChat(stored.cold, async ({ deliver, sent, row, answerNextPage }) => {
    const asked = reads(sent);
    answerNextPage(pageReply);
    await deliver(update(cursor + 40, cursor + 41, [card("first")]));
    await deliver(update(cursor + 41, cursor + 42, [card("second")]));
    assert.equal(reads(sent), asked + 1);
    assert.equal(row(replyId).attachCards?.some((item) => item.id === "first") ?? false, false);
  });
});

test("an update that follows lands, and one already seen is ignored", async () => {
  await withOpenChat(stored.cold, async ({ deliver, row, sent }) => {
    const asked = reads(sent);
    const count = row(replyId).attachCards?.length ?? 0;
    await deliver(update(cursor, cursor + 1, [card("fresh")]));
    await deliver(update(cursor, cursor + 1, [card("again")]));
    assert.equal(row(replyId).attachCards.length, count + 1);
    assert.equal(reads(sent), asked);
  });
});

test("a socket that lost updates reads the view once", async () => {
  await withOpenChat(stored.cold, async ({ deliverTogether, sent, answerNextPage }) => {
    const asked = reads(sent);
    answerNextPage(pageReply);
    const gap = { type: "transcript_gap", data: { session_id: session } };
    await deliverTogether([gap, gap]);
    assert.equal(reads(sent), asked + 1);
  });
});

test("the DAG panel's branches are asked for again after an attach", async () => {
  await withOpenChat(stored.cold, async ({ deliver, sent }) => {
    const asked = sent().filter((message) => message.action === "list_branches").length;
    await deliver({ type: "branches_changed", data: { session_id: session } });
    const requests = sent().filter((message) => message.action === "list_branches");
    assert.equal(requests.length, asked + 1);
    assert.notEqual(requests.at(-1).include_graph, false);
  });
});

const failed = recordings.provider_error;
const failedLoaded = failed.cold.find((frame) => frame.type === "session_loaded");
const failedReply = failedLoaded.data.turns.at(-1).rows.at(-1);
const withRow = (frames, fields) => frames.map((frame) => frame.type !== "session_loaded" ? frame : {
  ...frame,
  data: { ...frame.data, turns: frame.data.turns.map((turn) => ({ ...turn, rows: turn.rows.map((row) => row.id === failedReply.id ? { ...row, ...fields } : row) })) },
});

test("a recovered continuation error clears through its transcript update", async () => {
  await withOpenChat(failed.cold, async ({ deliver, row, sent }) => {
    assert.equal(row(failedReply.id).status, "error");
    const asked = reads(sent);
    const failedCursor = failedLoaded.data.window.revision;
    const failedTurn = failedLoaded.data.turns.at(-1).id;
    await deliver({ type: "transcript_update", data: {
      session_id: failed.session_id, view: failed.session_id, previous: failedCursor, revision: failedCursor + 1,
      changes: [{ kind: "row_patch", turnId: failedTurn, rowId: failedReply.id, fields: { status: "done", error: null } }],
    } });
    assert.equal(row(failedReply.id).status, "done");
    assert.equal(reads(sent), asked, "no read was asked for");
  });
});
