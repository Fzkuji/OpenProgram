import assert from "node:assert/strict";
import test from "node:test";
import { attemptLabel, cleanRunTitle, clockTime, dayName, durationWords, eventLabel, runOutcome, runStatusLine, runTitle } from "../../components/right-sidebar/running/activity-run-wording.ts";

const text = english => english;
const at = (hour, minute = 0, second = 0, day = 9) => new Date(2026, 10, day, hour, minute, second).getTime() / 1000;
const run = patch => ({
  id: "m1", message_id: "m1", user_message_id: "u1", execution_id: null, is_report: false, asked_by: "you", asked_preview: "", reply: "done",
  started_at: at(2), ended_at: at(2, 1, 44), execution_status: null, message_status: null, reason_code: null, task_outcome: null, task_text: null,
  event_count: 0, revision: "r", ...patch,
});
const status = (patch, options = {}) => { const item = run(patch); return runStatusLine(item, runOutcome(item, Boolean(options.waiting)), { now: at(2, 5), text, ...options }); };

test("the execution decides first, and completed still reads the reply tag", () => {
  assert.equal(runOutcome(run({ execution_status: "failed", reply: "[nothing new]" }), false), "failed");
  assert.equal(runOutcome(run({ execution_status: "running" }), false), "running");
  assert.equal(runOutcome(run({ execution_status: "cancelling" }), false), "running");
  assert.equal(runOutcome(run({ execution_status: "reconciliation_required" }), false), "interrupted");
  assert.equal(runOutcome(run({ execution_status: "completed", reply: "[could not check] page gone" }), false), "check-failed");
  assert.equal(runOutcome(run({ execution_status: "completed", reply: "[nothing new]" }), false), "nothing-new");
  assert.equal(runOutcome(run({ execution_status: "completed" }), false), "completed");
});

test("waiting beats everything, then the task record, then tags, then the message", () => {
  assert.equal(runOutcome(run({ execution_status: "paused" }), true), "waiting");
  assert.equal(runOutcome(run({ task_outcome: "failed" }), false), "check-failed");
  assert.equal(runOutcome(run({ task_outcome: "quiet", reply: "text" }), false), "nothing-new");
  assert.equal(runOutcome(run({ task_outcome: "skipped" }), false), "skipped");
  assert.equal(runOutcome(run({ reply: "[error] boom" }), false), "failed");
  assert.equal(runOutcome(run({ message_status: "paused", ended_at: null }), false), "paused");
  assert.equal(runOutcome(run({ message_status: "running", ended_at: null }), false), "unknown");
});

test("the status line starts with the state and the start time", () => {
  assert.deepEqual(status({ execution_status: "failed" }), { label: "Failed · 02:00 · 1m 44s", tone: "err", markTone: "err" });
  assert.deepEqual(status({ reply: "[could not check] x", ended_at: at(2, 0, 36) }), { label: "Check failed · 02:00 · 36s", tone: "warn", markTone: "warn" });
  assert.deepEqual(status({}), { label: "02:00 · 1m 44s", tone: "muted", markTone: "muted" });
  assert.equal(status({ message_status: "paused", ended_at: null }).label, "Paused · no end saved");
  assert.equal(status({ execution_status: "paused", execution_id: "e", ended_at: null }).label, "Paused · 02:00");
});

test("a message that paused with no end says so, whatever its tag says", () => {
  const paused = { reply: "[nothing new]", message_status: "paused", ended_at: null };
  assert.equal(runOutcome(run(paused), false), "paused");
  assert.equal(status(paused).label, "Paused · no end saved");
  assert.equal(runOutcome(run({ ...paused, reply: "[could not check] gone" }), false), "check-failed");
  assert.equal(runOutcome(run({ ...paused, reply: "[error] boom" }), false), "failed");
  assert.equal(runOutcome(run({ reply: "[nothing new]", message_status: "completed" }), false), "nothing-new");
});

test("an empty report goes by the name it was given, and never says no reply", () => {
  const empty = run({ is_report: true, reply: "", asked_preview: "" });
  assert.equal(runTitle(empty, text, "Hourly stock check"), "Hourly stock check");
  assert.equal(runTitle(empty, text), "Empty report");
  assert.equal(runTitle(run({ is_report: true, reply: "**Back in stock**\nNano" }), text, "ignored"), "Back in stock");
  assert.equal(runTitle(run({ reply: "", asked_preview: "" }), text), "No reply saved");
});

test("live runs say what they are doing", () => {
  assert.deepEqual(status({ execution_status: "running", ended_at: null }, { doing: "Running npm test" }), { label: "Running npm test · 5m 0s", tone: "run", markTone: "run" });
  assert.deepEqual(status({ execution_status: "paused", ended_at: null }, { waiting: true, waitingLabel: "Waiting for approval" }), { label: "Waiting for approval", tone: "warn", markTone: "warn" });
});

test("reports and empty task messages say what is missing", () => {
  assert.equal(status({ is_report: true, reply: "body" }).label, "Report · 02:00");
  assert.equal(status({ is_report: true, reply: "" }).label, "Report · 02:00 · no text saved");
  assert.equal(status({ task_outcome: "result", reply: "" }).label, "Sent you a message · no text saved");
});

test("a task run with no tip has only its end time", () => {
  assert.equal(status({ started_at: null, ended_at: at(1), task_outcome: "failed" }).label, "Check failed · 01:00");
  assert.equal(status({ started_at: null, ended_at: at(1), task_outcome: "skipped" }).label, "Skipped: the previous run was still going · 01:00");
  assert.equal(status({ started_at: null, ended_at: at(1), task_outcome: "missed" }).label, "Missed: OpenProgram was not running · 01:00");
});

test("durations and clock times", () => {
  assert.equal(durationWords(36, text), "36s");
  assert.equal(durationWords(104, text), "1m 44s");
  assert.equal(durationWords(7500, text), "2h 5m");
  assert.equal(clockTime(at(2, 1, 7)), "02:01");
  assert.equal(clockTime(at(2, 1, 7), true), "02:01:07");
});

test("day names count from today", () => {
  assert.equal(dayName(at(1), text, at(23)), "Today");
  assert.equal(dayName(at(23, 0, 0, 8), text, at(1)), "Yesterday");
  assert.match(dayName(at(23, 0, 0, 7), text, at(1)), /7/);
});

test("stored tags leave the title; an empty reply is never called nothing new", () => {
  assert.equal(cleanRunTitle("[nothing new]", text), "Nothing new");
  assert.equal(cleanRunTitle("[could not check] page **gone**", text), "page gone");
  assert.equal(cleanRunTitle("[error] boom", text), "boom");
  assert.equal(cleanRunTitle("", text), "No reply saved");
});

test("a run title is the first line of the reply, else what was asked", () => {
  assert.equal(runTitle(run({ reply: "**first**\nsecond" }), text), "first");
  assert.equal(runTitle(run({ reply: "", asked_preview: "the question\nmore" }), text), "the question");
  assert.equal(runTitle(run({ reply: "", task_text: "from the task" }), text), "from the task");
  assert.equal(runTitle(run({ reply: "" }), text), "No reply saved");
});

test("attempts name their state first", () => {
  assert.deepEqual(attemptLabel({ number: 1, status: "ended", outcome: "restart_recoverable" }, text), { label: "Paused · Attempt 1", tone: "muted", state: "paused" });
  assert.deepEqual(attemptLabel({ number: 2, status: "ended", outcome: "failed" }, text), { label: "Failed · Attempt 2", tone: "err", state: "failed" });
  assert.equal(attemptLabel({ number: 3, status: "active", outcome: null }, text).label, "Running · Attempt 3");
  assert.equal(attemptLabel({ number: 3, status: "ended", outcome: "completed" }, text).label, "Completed · Attempt 3");
  const ended = outcome => attemptLabel({ number: 1, status: "ended", outcome }, text);
  for (const outcome of ["waiting_at_safe_point", "step_at_safe_point", "paused_at_safe_point", "paused_before_first_effect", "owner_lost_after_checkpoint"]) assert.deepEqual(ended(outcome), { label: "Paused · Attempt 1", tone: "muted", state: "paused" }, outcome);
  for (const outcome of ["owner_lost", "owner_lost_before_checkpoint", "owner_lost_before_activation", "reconciliation_required", "restart_pending", "interrupted"]) assert.deepEqual(ended(outcome), { label: "Interrupted · Attempt 1", tone: "muted", state: "interrupted" }, outcome);
  for (const outcome of ["cancelled", "cancelled_at_safe_point", "owner_lost_during_cancel"]) assert.equal(ended(outcome).state, "cancelled", outcome);
  assert.deepEqual(ended("something_new"), { label: "Ended · Attempt 1", tone: "muted", state: "ended" });
  assert.equal(ended(null).tone, "muted");
});

test("event rows read the way the prototype words them", () => {
  assert.deepEqual(eventLabel({ kind: "asked", at: 1, by: "you", text: "hello", truncated: false }, text), { label: "Asked: hello", tone: "muted" });
  assert.deepEqual(eventLabel({ kind: "asked", at: 1, by: "check-in", text: "hello", truncated: false }, text), { label: "Check-in: hello", tone: "muted" });
  assert.deepEqual(eventLabel({ kind: "replied", at: 1, text: "[nothing new]", truncated: false }, text), { label: "Replied: Nothing new", tone: "muted" });
  assert.deepEqual(eventLabel({ kind: "step", at: 1, node_id: "n", tool_call_id: null, name: "bash", input: { command: "npm test\nmore" }, input_truncated: false, result: null, result_truncated: false, failed: false }, text), { label: "Ran npm test", tone: "muted" });
  assert.deepEqual(eventLabel({ kind: "step", at: 1, node_id: "n", tool_call_id: null, name: "bash", input: { command: "ls" }, input_truncated: false, result: "x", result_truncated: false, failed: true }, text), { label: "Failed · Ran ls", tone: "err" });
});

test("a browser step says what it did", () => {
  const step = patch => eventLabel({ kind: "step", at: 0, node_id: "n", tool_call_id: null, name: "computer_use_agent", input: {}, input_truncated: false, result: null, result_truncated: false, failed: false, ...patch }, text).label;
  assert.equal(step({ input: { actions: [{ action: "navigate", args: { url: "https://gomami.io/store/x" } }, { action: "evaluate", args: {} }] } }), "Browser: navigate gomami.io, evaluate");
  assert.equal(step({ result: "<guide>how to use</guide>" }), "Browser: guide");
  assert.equal(step({ failed: true, input: JSON.stringify({ action: "navigate", args: { url: "http://vps.hosting/" } }) }), "Failed · Browser: navigate vps.hosting");
});
