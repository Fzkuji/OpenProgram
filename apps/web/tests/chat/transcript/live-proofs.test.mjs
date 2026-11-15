/**
 * The convergence proofs of live delivery, run through the production socket
 * handler, reducers and row components over recordings of the real server.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { loadRecordings, replayRecording } from "../../_support/transcript-replay.mjs";

const RECORDINGS = fileURLToPath(new URL("./recordings", import.meta.url));
const recordings = loadRecordings(RECORDINGS);
const named = (name) => recordings.find((recording) => recording.name === name);
const frameOf = (step) => step.frame;
const isRowFrame = (frame) => frame?.type === "transcript_update"
  || (frame?.type === "chat_response" && ["stream_event", "text_end"].includes(frame.data?.type));

async function lastView(recording) {
  const checkpoints = await replayRecording(recording);
  return checkpoints.at(-1);
}

for (const recording of recordings) {
  test(`${recording.name}: the same frames delivered twice change nothing`, async () => {
    const once = await lastView(recording);
    const twice = await lastView({
      ...recording,
      timeline: recording.timeline.flatMap((step) => (isRowFrame(frameOf(step)) ? [step, step] : [step])),
    });
    assert.deepEqual(twice.mismatches.map((m) => `${m.kind} ${m.row} ${m.path}`), once.mismatches.map((m) => `${m.kind} ${m.row} ${m.path}`));
    assert.deepEqual(twice.live.ids, once.live.ids);
  });
}

test("a read during a stream causes no loop and no shrink", async () => {
  const recording = named("read_during_stream");
  const asked = recording.timeline.filter((step) => step.read).length;
  const checkpoints = await replayRecording(recording);
  for (const checkpoint of checkpoints) assert.deepEqual(checkpoint.mismatches, [], checkpoint.name);
  assert.equal(checkpoints.at(-1).reads, asked, "the only reads are the ones the chat made");
  const sizes = checkpoints.map((checkpoint) => Math.max(0, ...Object.values(checkpoint.live.rows).map((row) => (row.content ?? "").length)));
  assert.deepEqual(sizes, [...sizes].sort((a, b) => a - b));
});

test("a /spawn line stays the user's own row, with its result beside it", async () => {
  const recording = named("slash_spawn_action");
  const { mismatches, live } = await lastView(recording);
  assert.deepEqual(mismatches, []);
  const line = Object.values(live.rows).find((row) => row.role === "user" && row.content === "/task helper: do the slash task");
  assert.ok(line, "the typed line survives");
  assert.equal(line.display, undefined);
});

test("a fresh chat streams from its first frame", async () => {
  const recording = named("fresh_chat");
  const firstText = recording.timeline.findIndex((step) => step.frame?.data?.event?.type === "text");
  assert.ok(firstText > 0);
  const { live } = await lastView({ ...recording, timeline: recording.timeline.slice(0, firstText + 1) });
  const reply = Object.values(live.rows).find((row) => row.role === "assistant");
  assert.ok(reply?.content, "the reply already shows its first text");
});

test("a steer takes its place and its pending bubble clears", async () => {
  const { mismatches, live } = await lastView(named("steered_turn"));
  assert.deepEqual(mismatches, []);
  const steer = Object.values(live.rows).find((row) => row.steering);
  assert.ok(steer, "the steer is drawn");
  assert.ok(live.ids.indexOf(steer.id) < live.ids.indexOf(live.ids.find((id) => id.endsWith("_reply"))));
});

test("citations come from the real frame", async () => {
  const { live } = await lastView(named("provider_citations"));
  const reply = Object.values(live.rows).find((row) => row.role === "assistant");
  assert.ok(reply.blocks.some((block) => block.citations?.length));
});

test("a turn stopped before it started shows a stopped reply, live and after a reload", async () => {
  const { mismatches, live } = await lastView(named("stopped_before_start"));
  assert.deepEqual(mismatches, []);
  const reply = Object.values(live.rows).find((row) => row.role === "assistant");
  assert.equal(reply?.status, "cancelled");
  assert.equal(reply.content, "");
});

test("text stops after Stop and the terminal row is the last thing to change the reply", async () => {
  const { mismatches, live } = await lastView(named("stopped_during_tool"));
  assert.deepEqual(mismatches, []);
  assert.ok(Object.values(live.rows).some((row) => row.status === "cancelled"));
});
