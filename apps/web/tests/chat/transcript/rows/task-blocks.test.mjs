// Which part of a reply a block is drawn in: outside any task, in a tool
// group, or under a task and its step (lib/chat/transcript/task-blocks.ts).
import assert from "node:assert/strict";
import test from "node:test";

const {
  autoOpen, drawnState, lastTaskPart, layoutReply, normalizeSegments, currentStepId, placedAnchor, progressOf,
  railRange, rowTasks, tasksByRow, useTaskStore,
} = await import("../../../../lib/chat/transcript/task-blocks.ts");

const steps = (...statuses) => statuses.map((status, at) => ({
  id: String(14 + at), subject: `step ${at + 1}`, status, ...(status === "completed" ? { result: `did ${at + 1}` } : {}),
}));
const raw = (fields = {}) => ({
  anchor_id: "r1", plan_id: "T3", scope: "", title: "Fold the agent list", purpose: "Finished agents fold.",
  state: "running", outcome: "", advanced: true, current: "16", earlier: [],
  started_at: 100, ended_at: null, created_at: 100, updated_at: 160,
  todos: steps("completed", "completed", "in_progress", "pending", "pending"), ...fields,
});
const seg = (fields) => normalizeSegments([raw(fields)])[0];
const stored = (anchor, planId, statuses, fields = {}) => ({
  anchor_id: anchor, plan_id: planId, scope: "", created_at: 1, updated_at: 1,
  todos: statuses.map((status, at) => ({ id: String(Number(planId) + at), subject: `todo ${Number(planId) + at}`, status })), ...fields,
});
let call = 0;
const tool = (name, stamp = {}) => ({ type: "tool", tool: name, tool_call_id: `c${++call}`, ...stamp });
const say = (text, stamp = {}) => ({ type: "text", text, ...stamp });
const keys = (blocks) => blocks.map((_, at) => `b${at}`);
const layout = (blocks, segments = [], legacy = []) => layoutReply(blocks, keys(blocks), { segments, legacy });
const shape = (parts) => parts.map((part) => part.kind === "exec" ? `exec:${part.items.map(({ b }) => b.tool ?? b.type).join(",")}`
  : part.kind === "task" ? `task:${part.segment.taskId}` : `${part.kind}:${part.b.text}`);
const T3 = { task: "T3" };

test("a segment reads the wire shape; an entry with no title is a stored todo list", () => {
  const segment = seg();
  assert.deepEqual(
    [segment.anchorId, segment.taskId, segment.title, segment.state, segment.advanced, segment.current, segment.startedAt, segment.endedAt, segment.legacy],
    ["r1", "T3", "Fold the agent list", "running", true, "16", 100, null, false],
  );
  assert.deepEqual(segment.steps[0], { id: "14", subject: "step 1", status: "completed", result: "did 1" });
  assert.equal(seg({ todos: [{ id: "1", subject: "x", status: "dropped" }] }).steps[0].status, "dropped");
  const [old] = normalizeSegments([stored("r1", "1", ["completed", "pending"])]);
  assert.equal(old.legacy, true);
  assert.equal(normalizeSegments([{ anchor_id: "r1", plan_id: "1", todos: [] }]).length, 0, "a stored list with no todo draws nothing");
  assert.equal(normalizeSegments([raw({ todos: [] })]).length, 1, "a task may have no steps");
  assert.deepEqual(normalizeSegments(undefined), []);
});

test("a reply with no task gives the text and execution parts it always gave", () => {
  const blocks = [say("first"), { type: "thinking", text: "hm" }, tool("grep"), tool("todo_update"), tool("edit"),
    { type: "steering", text: "wait" }, tool("bash"), say("done")];
  const parts = layout(blocks);
  assert.deepEqual(shape(parts), ["text:first", "exec:thinking,grep,todo_update,edit", "steering:wait", "exec:bash", "text:done"]);
  assert.equal(parts[1].key, "exec:b1");
  assert.deepEqual(parts[1].items.map(({ i }) => i), [1, 2, 3, 4]);
});

test("work goes under the step its stamp names; the task tools are never drawn", () => {
  const blocks = [
    say("I'll look first."),
    tool("task_start", { task: "T3", step: "14" }),
    tool("grep", { task: "T3", step: "14" }),
    say("The list is in agent-list.tsx.", { task: "T3", step: "14" }),
    tool("task_step", { task: "T3", step: "15" }),
    tool("edit", { task: "T3", step: "15" }),
    tool("task_step"),
    say("Finished agents now fold."),
  ];
  const parts = layout(blocks, [seg()]);
  assert.deepEqual(shape(parts), ["text:I'll look first.", "task:T3", "text:Finished agents now fold."]);
  const task = parts[1];
  assert.equal(task.flat, false);
  assert.deepEqual(task.steps.map((row) => row.items.map(({ b }) => b.tool ?? b.text)),
    [["grep", "The list is in agent-list.tsx."], ["edit"], [], [], []]);
  assert.deepEqual(task.items.map(({ i }) => i), [2, 3, 5]);
});

test("stamped text after the task's last real call stays outside the block", () => {
  const blocks = [tool("task_start", T3), tool("grep", { ...T3, step: "14" }), say("between", { ...T3, step: "14" }),
    tool("task_step", { ...T3, step: "15" }), say("answer", { ...T3, step: "15" })];
  assert.deepEqual(shape(layout(blocks, [seg()])), ["task:T3", "text:between", "text:answer"]);
  // The next real call pulls the text before it into the block.
  const more = [...blocks, tool("edit", { ...T3, step: "15" })];
  const parts = layout(more, [seg()]);
  assert.deepEqual(shape(parts), ["task:T3"]);
  assert.deepEqual(parts[0].steps[1].items.map(({ b }) => b.tool ?? b.text), ["answer", "edit"]);
});

test("thinking after the last call is the step's work until the task is closed", () => {
  const think = { type: "thinking", text: "hm", ...T3, step: "14" };
  const blocks = [tool("task_start", T3), tool("grep", { ...T3, step: "14" }), say("found", { ...T3, step: "14" }), think];
  // Being worked on, or stopped there: no separate strip under the block.
  const open = layout(blocks, [seg()]);
  assert.deepEqual(shape(open), ["task:T3"]);
  assert.deepEqual(open[0].steps[0].items.map(({ b }) => b.tool ?? b.type), ["grep", "text", "thinking"]);
  // Text alone never extends the block: the final answer stays outside.
  assert.deepEqual(shape(layout([...blocks, say("answer", { ...T3, step: "14" })], [seg()])), ["task:T3", "text:answer"]);
  // Closed: the block ends at its last real call.
  assert.deepEqual(shape(layout(blocks, [seg({ state: "done" })])), ["task:T3", "text:found", "exec:thinking"]);
  // Thinking before any real call already stands in the block.
  assert.deepEqual(shape(layout([tool("task_start", T3), think], [seg()])), ["task:T3"]);
});

test("an elapsed time reads in the interface language", async () => {
  const { formatElapsed } = await import("../../../../lib/format-utils/elapsed.ts");
  const zh = (_en, text) => text;
  const en = (text) => text;
  assert.deepEqual([42, 134, 240, 3900, 3600].map((s) => formatElapsed(s, zh)),
    ["42 秒", "2 分 14 秒", "4 分钟", "1 小时 5 分", "1 小时"]);
  assert.deepEqual([42, 134, 3900].map((s) => formatElapsed(s, en)), ["42s", "2m 14s", "1h 5m"]);
  assert.equal(formatElapsed(134), "2m 14s");
});

test("a stamp whose segment has not arrived counts as none, and joins once it does", () => {
  const blocks = [tool("task_start", T3), tool("grep", { ...T3, step: "14" }), say("found", { ...T3, step: "14" }), tool("edit", { ...T3, step: "14" })];
  assert.deepEqual(shape(layout(blocks)), ["exec:grep", "text:found", "exec:edit"]);
  assert.deepEqual(shape(layout(blocks, [seg()])), ["task:T3"]);
});

test("a second task in the reply draws after the first, with the text between them outside", () => {
  const first = seg({ state: "displaced", current: "17" });
  const second = seg({ plan_id: "T4", title: "Fix the crash", todos: steps("in_progress", "pending"), current: "14" });
  const blocks = [
    tool("task_start", T3), tool("grep", { ...T3, step: "14" }),
    say("Switching to the crash.", { ...T3, step: "14" }),
    tool("task_start", { task: "T4", step: "14" }), tool("bash", { task: "T4", step: "14" }),
  ];
  const parts = layout(blocks, [first, second]);
  assert.deepEqual(shape(parts), ["task:T3", "text:Switching to the crash.", "task:T4"]);
  assert.equal(lastTaskPart(parts).segment.taskId, "T4");
});

test("a task that ended without advancing, or has no steps, lays its work flat", () => {
  const blocks = [tool("task_start", T3), tool("grep", T3), say("note", T3), tool("edit", T3)];
  // Just started, on its first step: the work is that step's.
  const started = layout(blocks, [seg({ advanced: false, todos: steps("in_progress", "pending") })])[0];
  assert.equal(started.flat, false);
  assert.deepEqual(started.steps.map((row) => row.items.length), [3, 0]);
  const never = layout(blocks, [seg({ advanced: false, state: "done", todos: steps("in_progress", "pending") })])[0];
  assert.equal(never.flat, true);
  assert.equal(never.steps.length, 2, "the steps stay as an outline");
  assert.deepEqual(never.steps.map((row) => row.items.length), [0, 0]);
  assert.deepEqual(never.items.map(({ b }) => b.tool ?? b.text), ["grep", "note", "edit"]);
  const bare = layout(blocks, [seg({ todos: [] })])[0];
  assert.deepEqual([bare.flat, bare.steps.length, bare.items.length], [true, 0, 3]);
});

test("steps finished in earlier turns leave the outline and are counted as one row", () => {
  const segment = seg({ earlier: ["14", "15"] });
  const part = layout([tool("bash", { ...T3, step: "16" })], [segment])[0];
  assert.equal(part.earlier, 2);
  assert.deepEqual(part.steps.map((row) => row.step.id), ["16", "17", "18"]);
  assert.deepEqual(part.steps[0].items.map(({ b }) => b.tool), ["bash"]);
});

test("a segment that arrived before its blocks is the last part of the reply", () => {
  assert.deepEqual(shape(layout([say("starting")], [seg()])), ["text:starting", "task:T3"]);
  // Only the task call so far: the block stands where the call is.
  assert.deepEqual(shape(layout([tool("task_start", T3), say("then", T3)], [seg()])), ["task:T3", "text:then"]);
});

test("a stored todo list draws once at the reply's first todo call, and that reply's todo calls go", () => {
  const [list] = normalizeSegments([stored("r1", "1", ["completed", "pending"])]);
  const blocks = [tool("grep"), tool("todo_create"), tool("edit"), tool("todo_update"), say("ok")];
  const parts = layout(blocks, [], [list]);
  assert.deepEqual(shape(parts), ["exec:grep", "task:1", "exec:edit", "text:ok"]);
  assert.deepEqual([parts[1].flat, parts[1].steps.length, parts[1].items.length], [false, 2, 0]);
  // No todo call in the reply: nothing to place, the calls keep their summary row.
  assert.deepEqual(shape(layout([tool("grep"), say("ok")], [], [list])), ["exec:grep", "text:ok"]);
});

test("the drawn state comes from the segment, and for a running one from the reply's status", () => {
  const table = [
    ["running", "streaming", "running"], ["running", "cancelling", "running"],
    // Paused waits on the person: the turn has not ended.
    ["running", "cancelled", "stopped"], ["running", "paused", "running"],
    ["running", "error", "failed"], ["running", "interrupted", "interrupted"],
    ["running", "done", "carried"], ["running", undefined, "carried"],
    ["done", "cancelled", "done"], ["carried", "error", "carried"], ["displaced", "streaming", "displaced"],
  ];
  for (const [state, status, drawn] of table) assert.equal(drawnState(seg({ state }), status), drawn, `${state} under ${status}`);
  assert.equal(drawnState(seg(), "streaming", true), "carried", "a settled execution ends the run before the row says so");
  const [all] = normalizeSegments([stored("r1", "1", ["completed", "completed"])]);
  const [some] = normalizeSegments([stored("r1", "1", ["completed", "pending"])]);
  assert.deepEqual([drawnState(all, "done"), drawnState(some, "done")], ["done", "carried"]);
});

test("progress leaves dropped steps out and is absent for a task that never advanced", () => {
  assert.deepEqual(progressOf(seg()), { done: 2, total: 5, on: 3 });
  assert.deepEqual(progressOf(seg({ todos: [...steps("completed", "in_progress"), { id: "9", subject: "x", status: "dropped" }] })), { done: 1, total: 2, on: 2 });
  assert.deepEqual(progressOf(seg({ advanced: false, todos: steps("in_progress", "pending") })), { done: 0, total: 2, on: 1 }, "a task on its first step");
  assert.equal(progressOf(seg({ advanced: false, state: "done" })), null, "no N/N is made up");
  assert.equal(progressOf(seg({ todos: [] })), null);
  assert.deepEqual(progressOf(normalizeSegments([stored("r1", "1", ["completed", "pending"])])[0]), { done: 1, total: 2, on: 1 });
});

test("a block opens by itself while it runs last, when it stopped, or while its sub-agent runs", () => {
  assert.equal(autoOpen("running", true, false), true);
  assert.equal(autoOpen("running", false, false), false, "a later task in the reply folds the earlier one");
  for (const drawn of ["stopped", "failed", "interrupted"]) assert.equal(autoOpen(drawn, false, false), true);
  for (const drawn of ["done", "carried", "displaced"]) assert.equal(autoOpen(drawn, true, false), false);
  assert.equal(autoOpen("done", true, true), true);
  assert.equal(autoOpen("carried", true, false, true), false);
  assert.equal(currentStepId(seg(), "running"), "16");
  assert.equal(currentStepId(seg({ current: "15" }), "stopped"), "15", "a stopped block is on the step it stopped on");
  assert.equal(currentStepId(seg(), "done"), null);
});

test("segments land on their reply; a cut-off reply's go to the reply that carries it on", () => {
  const rows = { r1: { id: "r1", continuedBy: "r2" }, r2: { id: "r2" }, r3: { id: "r3" } };
  assert.equal(placedAnchor("r1", rows), "r2");
  const segments = normalizeSegments([
    raw({ anchor_id: "r1", updated_at: 150, started_at: 100 }),
    raw({ anchor_id: "r2", updated_at: 200, started_at: 180, state: "carried", todos: steps("completed", "completed", "completed", "pending", "pending") }),
    raw({ anchor_id: "r3", earlier: ["14", "15", "16"] }),
    raw({ anchor_id: "gone", plan_id: "T9" }),
  ]);
  const byRow = tasksByRow(segments, ["r1", "r2", "r3"], rows);
  assert.deepEqual([...byRow.keys()], ["r2", "r3"]);
  const [merged] = byRow.get("r2").segments;
  assert.deepEqual([merged.anchorId, merged.state, merged.startedAt, merged.updatedAt], ["r2", "carried", 100, 200]);
  assert.deepEqual(byRow.get("r3").earlierAnchor, { T3: "r2" }, "the jump row leads to the task's previous segment");
  assert.equal(rowTasks(segments, ["r1", "r2", "r3"], rows, "r1").segments.length, 0);
});

test("a stored todo list is drawn under the first loaded reply that has it, as it was last left", () => {
  const rows = { r1: { id: "r1" }, r2: { id: "r2" }, r3: { id: "r3" } };
  const segments = normalizeSegments([
    stored("r1", "1", ["in_progress", "pending"], { updated_at: 1 }),
    stored("r2", "1", ["completed", "completed"], { updated_at: 2 }),
    stored("r3", "7", ["pending"], { updated_at: 3 }),
  ]);
  const byRow = tasksByRow(segments, ["r1", "r2", "r3"], rows);
  assert.deepEqual(byRow.get("r1").legacy.map((list) => list.steps.map((step) => step.status)), [["completed", "completed"]]);
  assert.equal(byRow.get("r2"), undefined, "the later reply does not draw the list again");
  assert.deepEqual(byRow.get("r3").legacy.map((list) => list.taskId), ["7"]);
  // The first reply is not loaded: the list draws under the next one that is.
  assert.deepEqual([...tasksByRow(segments, ["r2", "r3"], rows).keys()], ["r2", "r3"]);
});

test("the message rail names a task's steps, or the range a reply covered", () => {
  assert.deepEqual(railRange(seg({ state: "done", todos: steps("completed", "completed", "completed") })), { steps: 3 });
  assert.deepEqual(railRange(seg({ state: "carried", todos: steps("completed", "completed", "pending") })), { from: 1, to: 2 });
  assert.deepEqual(railRange(seg({ state: "running", earlier: ["14", "15"], todos: steps("completed", "completed", "in_progress", "pending") })), { from: 3, to: 4 });
});

test("a load replaces a session's segments; an update replaces the segment of its reply and task", () => {
  const store = useTaskStore.getState();
  store.setAll("a", [raw(), stored("r0", "1", ["pending"])]);
  store.setAll("b", []);
  store.upsert("a", [raw({ state: "done", outcome: "folded" }), raw({ anchor_id: "r2" })]);
  const held = useTaskStore.getState().segmentsBySession.a;
  assert.deepEqual(held.map((segment) => `${segment.anchorId}:${segment.taskId}:${segment.state}`), ["r1:T3:done", "r0:1:running", "r2:T3:running"]);
  assert.deepEqual(useTaskStore.getState().segmentsBySession.b, []);
  const before = useTaskStore.getState().segmentsBySession;
  store.upsert("a", undefined);
  assert.equal(useTaskStore.getState().segmentsBySession, before, "a frame with nothing changed keeps the list");
});
