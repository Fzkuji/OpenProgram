/**
 * A chat watched live and the same chat opened from its stored transcript show
 * the same rows. Recordings come from the real server. A disagreement the
 * design has not fixed yet is listed under its finding id and reported as todo;
 * any other disagreement fails. When a finding is fixed its mark must go.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { compareViews, compareWithCold, loadRecordings, openStored, replayRecording } from "../../_support/transcript-replay.mjs";

const RECORDINGS = fileURLToPath(new URL("./recordings", import.meta.url));

/** Known defects, first match wins. `only` lists the recordings, or
 *  "recording / checkpoint" places, that reproduce a mark, and every place
 *  listed must still reproduce it; the same symptom anywhere else fails.
 *  Every defect this file has found so far is fixed, so the list is empty. */
const KNOWN = [];

const placeOf = (recording, checkpoint) => `${recording.name} / ${checkpoint.name}`;

function ruleFor(mismatch, recording, checkpoint) {
  return KNOWN.find((entry) => (entry.only.includes(recording.name) || entry.only.includes(placeOf(recording, checkpoint)))
    && entry.matches(mismatch));
}

function attribute(recording, checkpoint) {
  const open = [];
  const known = new Map();
  const add = (finding, item) => known.set(finding, [...(known.get(finding) ?? []), item]);
  const byRow = new Map();
  for (const mismatch of checkpoint.mismatches) {
    if (mismatch.kind === "markup") continue;
    const rule = ruleFor(mismatch, recording, checkpoint);
    if (!rule) { open.push(mismatch); continue; }
    add(rule.finding, mismatch);
    byRow.set(mismatch.row, [...(byRow.get(mismatch.row) ?? []), rule.finding]);
  }
  // Drawn markup is known only when the row's known field differences account
  // for all of it; anything else about how the row draws is a new defect.
  for (const mismatch of checkpoint.mismatches.filter((m) => m.kind === "markup")) {
    const findings = byRow.get(mismatch.row);
    if (!findings || !mismatch.explained) open.push(mismatch);
    else for (const finding of new Set(findings)) add(finding, mismatch);
  }
  return { open, known };
}

function brief(mismatch) {
  const clip = (value) => { const text = JSON.stringify(value) ?? "undefined"; return text.length > 300 ? `${text.slice(0, 300)}...` : text; };
  return `${mismatch.kind} ${mismatch.row} ${mismatch.path} live=${clip(mismatch.live)} cold=${clip(mismatch.cold)}`;
}

/** What a replay found, without the values that vary between runs. */
const signature = (checkpoints) => checkpoints.map(({ name, mismatches, stranded }) => ({
  name, stranded, found: mismatches.map((m) => `${m.kind} ${m.row} ${m.path} ${m.explained ?? ""}`).sort(),
}));

const recordings = loadRecordings(RECORDINGS);
const seen = new Map();
const seenPlaces = new Set();
const results = new Map();
for (const recording of recordings) {
  const checkpoints = await replayRecording(recording);
  results.set(recording.name, signature(checkpoints));
  for (const checkpoint of checkpoints) {
    const { open, known } = attribute(recording, checkpoint);
    for (const [finding, items] of known) {
      seen.set(finding, [...(seen.get(finding) ?? []), `${placeOf(recording, checkpoint)}: ${items.map(brief).join("; ")}`]);
      seenPlaces.add(`${finding} ${recording.name}`).add(`${finding} ${placeOf(recording, checkpoint)}`);
    }
    test(`${recording.name}, ${checkpoint.name}: live equals cold`, () => {
      assert.deepEqual(open.map(brief), []);
    });
  }
  test(`${recording.name}: every read the client made was answered`, () => {
    assert.equal(checkpoints.at(-1).stranded, 0);
  });
}

for (const { finding, title } of KNOWN) {
  test(`${finding} ${title}`, { todo: `${finding} is not fixed yet` }, () => {
    assert.deepEqual(seen.get(finding) ?? [], []);
  });
}

test("every marked finding still reproduces wherever it is marked", () => {
  const stale = KNOWN.flatMap(({ finding, only }) => only.filter((place) => !seenPlaces.has(`${finding} ${place}`)).map((place) => `${finding} at ${place}`));
  assert.deepEqual(stale, [], "these findings no longer reproduce here, so remove their marks");
});

test("a recording's result does not depend on which recordings ran before it", async () => {
  for (const recording of [...recordings].reverse()) {
    assert.deepEqual(signature(await replayRecording(recording)), results.get(recording.name), recording.name);
  }
});

const row = (id, fields = {}) => ({ id, role: "assistant", content: id, ...fields });
const view = (ids) => ({ ids, rows: Object.fromEntries(ids.map((id) => [id, row(id)])), dom: Object.fromEntries(ids.map((id) => [id, id])) });

test("a window that skips a row between the rows it holds reports it", () => {
  assert.deepEqual(compareViews(view(["a", "c"]), view(["a", "b", "c"]), { partial: true }).map((m) => `${m.kind}:${m.row}`), ["only-cold:b"]);
});

test("a window may stop short of either end of the transcript", () => {
  assert.deepEqual(compareViews(view(["b", "c"]), view(["a", "b", "c", "d"]), { partial: true }), []);
});

test("markup counts as explained only when the row's differing fields draw it", async () => {
  const stored = recordings.find((recording) => recording.name === "fresh_chat").cold;
  const live = await openStored(stored);
  const id = live.ids.find((rowId) => !rowId.endsWith("_reply"));
  live.rows[id].content = "a different question";
  live.dom[id] += "<extra></extra>";
  const unexplained = await compareWithCold(live, stored, { partial: false });
  assert.deepEqual(unexplained.map((m) => `${m.kind}:${m.explained ?? ""}`), ["field:", "markup:false"]);
  const [frame] = stored;
  const turns = frame.data.turns.map((turn) => ({ ...turn, rows: turn.rows.map((row) => (row.id === id ? { ...row, content: "a different question" } : row)) }));
  live.dom[id] = (await openStored([{ ...frame, data: { ...frame.data, turns } }])).dom[id];
  const explained = await compareWithCold(live, stored, { partial: false });
  assert.deepEqual(explained.map((m) => `${m.kind}:${m.explained ?? ""}`), ["field:", "markup:true"]);
});
