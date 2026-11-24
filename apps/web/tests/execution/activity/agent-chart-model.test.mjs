// What an Agent's time chart draws, without a page around it.
import assert from "node:assert/strict";
import test from "node:test";
import { barColor, buildChart, buildWorkChart, levelsOf, MAIN_COLOR, chartWindow, columnsFor, focusOn, forEachNode, opensInPlace, paintColor, panelWidthFor, readableInput, sizeChart, stepFocus, travelMs, COLUMN_WIDTH, LEVELS, PANEL_INSET, PANEL_SNAP_WIDTHS } from "../../../components/right-sidebar/running/agent-chart/agent-chart-model.ts";

const NOW = 1_794_000_000;
const text = english => english;
const run = (id, started_at, extra = {}) => ({ id, message_id: id, user_message_id: `u_${id}`, execution_id: null, is_report: false, asked_by: "you", asked_preview: `asked ${id}`,
  reply: `Reply ${id}`, started_at, ended_at: started_at + 30, execution_status: null, message_status: "completed", reason_code: null, task_outcome: null, task_text: null, steps: [], ...extra });
const topic = (id, order, title = `Topic ${id}`) => ({ topic_id: id, topic_order: order, topic_title: title });
const step = (at, name, command, extra = {}) => ({ kind: "step", at, node_id: `${name}_${at}`, name, input: { command, path: command }, failed: false, result: null, ...extra });
const chart = (runs, extra = {}) => buildChart(runs, { text, now: NOW, waitOf: () => null, hasActions: () => false, liveCalls: () => [], reportName: () => null, nextWake: null, earlier: 0, ...extra });
const topics = roots => roots.filter(root => root.kind === "topic").map(root => root.node);
const hueOf = node => node.paint.hue;

test("a topic that comes back is drawn again in its own colour", () => {
  const roots = chart([run("c", NOW - 100, topic("t1", 0)), run("b", NOW - 200, topic("t2", 1)), run("a", NOW - 300, topic("t1", 0))]);
  const [newest, middle, oldest] = topics(roots);
  assert.equal(newest.title, "Topic t1");
  assert.equal(oldest.title, "Topic t1");
  assert.equal(paintColor(newest.paint), paintColor(oldest.paint));
  assert.notEqual(paintColor(newest.paint), paintColor(middle.paint));
});

test("messages are shades of their topic, steps shades of their message", () => {
  const runs = [run("b", NOW - 100, { ...topic("t1", 0), steps: [step(NOW - 95, "bash", "ls"), step(NOW - 90, "read", "a.py", { name: "read" })] }), run("a", NOW - 200, topic("t1", 0))];
  const [node] = topics(chart(runs));
  const [newer, older] = node.kids;
  assert.notEqual(paintColor(newer.paint), paintColor(older.paint));
  for (const message of node.kids) assert.ok(Math.abs(hueOf(message) - hueOf(node)) <= 10);
  for (const row of newer.kids) {
    assert.ok(Math.abs(hueOf(row) - hueOf(newer)) <= 4);
    assert.ok(row.paint.lift > newer.paint.lift);
  }
});

test("a failure keeps the topic's colour and says why under the step", () => {
  const failed = step(NOW - 90, "bash", "pytest", { failed: true, result: "2 tests timed out" });
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), execution_status: "failed", steps: [failed] })]));
  const [message] = node.kids;
  assert.match(message.line, /^Failed · 30s$/);
  const [row] = message.kids;
  assert.match(row.line, /^Failed · /);
  assert.equal(row.detail, "2 tests timed out");
  assert.ok(!paintColor(row.paint).includes("#"));
  assert.equal(row.paint.order, node.paint.order);
});

test("a finished message says how long it took and what it did, and what of it failed", () => {
  const steps = [step(NOW - 95, "bash", "pytest", { failed: true, result: "no" }), step(NOW - 90, "bash", "pytest"), step(NOW - 85, "edit", "a.py", { name: "edit" })];
  let [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]));
  assert.equal(node.kids[0].line, "30s · 2 commands, 1 edit, 1 failed");
  assert.equal(node.kids[0].strong, true);
  [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps: steps.slice(1) })]));
  assert.deepEqual([node.kids[0].line, node.kids[0].strong], ["30s · 1 command, 1 edit", false]);
});

test("calls of one function in a row share a line, each call one column right", () => {
  const steps = Array.from({ length: 22 }, (_, index) => step(NOW - 90 + index, "bash", `cmd ${index}`));
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]));
  const [group, ...others] = node.kids[0].kids;
  assert.equal(others.length, 0);
  assert.match(group.line, / · 22 commands$/);
  assert.equal(group.kids.length, 22);
  assert.equal(group.kids[0].depth, 3);
  assert.match(group.kids[0].line, /cmd 21/);
});

test("a failure splits the run of calls, so it stays in the Steps column", () => {
  const steps = [step(NOW - 95, "bash", "a"), step(NOW - 94, "bash", "b"), step(NOW - 93, "bash", "c", { failed: true, result: "x" }), step(NOW - 92, "bash", "d")];
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]));
  assert.deepEqual(node.kids[0].kids.map(row => Boolean(row.kids)), [false, false, true]);
});

test("a sub-agent's own steps are one column right", () => {
  const agent = { kind: "subagent", at: NOW - 90, ended_at: NOW - 70, job_id: "j", name: "reviewer", status: "completed", never_started: false, failed: false, error: null, children: [], conversation: null,
    steps: [step(NOW - 88, "read", "settings.json"), { kind: "replied", at: NOW - 75, text: "No conflicts" }] };
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps: [agent] })]));
  const [row] = node.kids[0].kids;
  assert.match(row.line, /Sub-agent · reviewer/);
  assert.equal(row.kids.length, 2);
  assert.match(row.kids[0].line, /^Replied: No conflicts/);
});

test("a message with no task yet has no title line, only what was asked, and the time is its latest moment", () => {
  const [node] = topics(chart([run("a", NOW - 100, topic("t1", 0))]));
  assert.deepEqual([node.kids[0].title, node.kids[0].words], [undefined, "asked a"]);
  // A pasted email's rule above its headers is not what was asked.
  const [pasted] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), asked_preview: "________\nFrom: Workshop chairs" })]));
  assert.equal(pasted.kids[0].words, "From: Workshop chairs");
  assert.equal(node.time, node.kids[0].time);
  assert.equal(node.kids[0].time, new Date((NOW - 70) * 1000).toTimeString().slice(0, 5));
});

test("an unsorted message joins the one before it, and with none before it waits", () => {
  let [node] = topics(chart([run("b", NOW - 100), run("a", NOW - 200, topic("t1", 0))]));
  assert.equal(node.title, "Topic t1");
  assert.equal(node.kids.length, 2);
  // A topic not named yet is room, never a stand-in name.
  [node] = topics(chart([run("a", NOW - 200)]));
  assert.deepEqual([node.title, node.hidden], ["", true]);
  assert.equal(node.paint.order, null);
});

test("runs nobody asked for sit apart, in grey", () => {
  const roots = topics(chart([run("b", NOW - 100, { asked_by: "schedule" }), run("a", NOW - 200, topic("t1", 0))]));
  assert.deepEqual(roots.map(node => node.title), ["Automatic runs", "Topic t1"]);
  assert.match(paintColor(roots[0].paint), /oklch\([\d.]+ 0\.000/);
});

test("a long silence and a new day are marked across the columns", () => {
  const roots = chart([run("b", NOW - 100, topic("t1", 0)), run("a", NOW - 100 - 14 * 3600, topic("t1", 0))]);
  const gap = roots.find(root => root.kind === "gap" && root.id.startsWith("gap:"));
  assert.equal(gap.idle, "14h idle");
  assert.equal(typeof gap.day, "string", "a new day's date goes in the time column");
  assert.ok(gap.day.length <= 8, "short enough to stay left of the bars");
  assert.equal(topics(roots).length, 2);
});

test("a running message says what it is doing now and for how long, and asks for nothing", () => {
  const live = run("a", NOW - 125, { ...topic("t1", 0), ended_at: null, execution_id: "e", execution_status: "running", steps: [step(NOW - 60, "bash", "build")] });
  const line = extra => topics(chart([live], { hasActions: () => true, ...extra }))[0].kids[0].line;
  let [node] = topics(chart([live], { hasActions: () => true }));
  assert.deepEqual([node.kids[0].line, node.kids[0].hasActions, node.kids[0].live], ["Working · 2m 5s", false, true]);
  assert.equal(node.line, "Running · 1 message");
  // Never the last step it took, as if it were taking it now.
  assert.equal(line({ livePhase: () => ({ kind: "thinking" }) }), "Thinking · 2m 5s");
  assert.equal(line({ livePhase: () => ({ kind: "writing" }) }), "Writing the reply · 2m 5s");
  assert.equal(line({ livePhase: () => ({ kind: "retrying", attempt: 2, max: 3 }) }), "Retrying (2/3) · 2m 5s");
  // A tool the reply runs, read from the reply itself: a command by its name, any other step in its own words.
  assert.equal(line({ livePhase: () => ({ kind: "tool", command: "cd /tmp && sleep 12; echo done" }) }), "Running sleep 12 · 2m 5s");
  assert.equal(line({ livePhase: () => ({ kind: "tool", step: "Read notes.md" }) }), "Read notes.md · 2m 5s");
  assert.equal(line({ livePhase: () => ({ kind: "thinking" }), liveCalls: () => [{ id: "c", at: NOW - 12, label: "xelatex -interaction=nonstopmode pres.tex && xelatex pres.tex" }] }), "Running xelatex pres.tex · 2m 5s");
  [node] = topics(chart([{ ...live, execution_status: "queued" }]));
  assert.equal(node.kids[0].line, "Queued");
  [node] = topics(chart([{ ...live, execution_status: "cancelling" }]));
  assert.equal(node.kids[0].line, "Stopping… · 2m 5s");
});

test("a message waiting on the reader shows its question and one button, and a paused one offers to go on", () => {
  const live = run("a", NOW - 125, { ...topic("t1", 0), ended_at: null, execution_id: "e", execution_status: "running", steps: [step(NOW - 60, "bash", "build")] });
  let [node] = topics(chart([live], { hasActions: () => true, waitOf: () => ({ waitLabel: "Waiting for your answer", question: "Overwrite it?\nIt has notes.", answerLabel: "Answer in chat" }) }));
  assert.deepEqual([node.kids[0].words, node.kids[0].line, node.kids[0].hasActions, node.kids[0].live], ["“Overwrite it?”", "Waiting for your answer · 1m 0s", true, true]);
  assert.equal(node.line, "Waiting for you · 1 message");
  [node] = topics(chart([{ ...live, execution_status: "paused" }], { hasActions: () => true }));
  assert.deepEqual([node.kids[0].line, node.kids[0].hasActions], ["Paused · 1m 5s", true]);
  assert.equal(node.line, "Paused · 1 message");
  [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), execution_status: "failed" })], { hasActions: () => true }));
  assert.equal(node.kids[0].hasActions, false);
  [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), message_status: "cancelled" })]));
  assert.equal(node.kids[0].line, "Stopped by you · 30s");
});

test("the next wake is on top and earlier runs come last", () => {
  const roots = chart([run("a", NOW - 100, topic("t1", 0))], { nextWake: { at: NOW + 60, line: "Always-on · hourly" }, earlier: 50 });
  assert.equal(roots[0].kind, "next");
  assert.equal(roots.at(-1).label, "Show 50 earlier");
});

test("a parent is as tall as its children at every deeper level, so the columns line up", () => {
  const steps = Array.from({ length: 6 }, (_, index) => step(NOW - 90 + index * 5, index % 2 ? "bash" : "read", `c${index}`));
  const [node] = topics(chart([run("b", NOW - 300, { ...topic("t1", 0), steps }), run("a", NOW - 900, topic("t1", 0))]));
  for (let level = 1; level < LEVELS; level += 1) {
    const sum = node.kids.reduce((total, kid) => total + kid.heights[level], 0);
    assert.ok(Math.abs(sum - node.heights[level]) < 1, `level ${level}`);
  }
});

test("a group's calls are capped at four rows' height and scroll in their cell", () => {
  const steps = Array.from({ length: 22 }, (_, index) => step(NOW - 90 + index, "bash", `cmd ${index}`));
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]));
  const group = node.kids[0].kids[0];
  assert.ok(group.heights[3] <= 120);
  forEachNode(group, kid => assert.ok(kid.heights[3] > 0));
});

test("the sidebar rests where one, two or three columns fit whole, the first its default", () => {
  assert.deepEqual(PANEL_SNAP_WIDTHS, [288, 544, 800]);
  PANEL_SNAP_WIDTHS.forEach((width, index) => assert.equal(columnsFor(width - PANEL_INSET), index + 1));
  assert.equal(panelWidthFor(1) - PANEL_INSET, COLUMN_WIDTH);
});

test("the column count follows the width alone, and a column is never shown in part", () => {
  assert.equal(columnsFor(200), 1, "narrower than a column is still one");
  assert.equal(columnsFor(256), 1);
  assert.equal(columnsFor(510), 1, "just short of two stretches the one");
  assert.equal(columnsFor(512), 2);
  assert.equal(columnsFor(700), 2);
  assert.equal(columnsFor(768), 3);
  assert.equal(columnsFor(5000), 3, "three at most");
});

test("the view ends on the deepest column the reader went to, or starts at the first", () => {
  assert.deepEqual(chartWindow(0, 1), { first: 0, last: 0 });
  assert.deepEqual(chartWindow(2, 1), { first: 2, last: 2 });
  assert.deepEqual(chartWindow(0, 2), { first: 0, last: 1 });
  assert.deepEqual(chartWindow(3, 2), { first: 2, last: 3 });
  assert.deepEqual(chartWindow(0, 3), { first: 0, last: 2 });
  assert.deepEqual(chartWindow(3, 3), { first: 1, last: 3 });
  assert.equal(chartWindow(1, 1).last, chartWindow(1, 2).last, "widening keeps the column the reader is on in view");
});

test("opening a block moves the view only when its next column is out of view", () => {
  assert.equal(focusOn(0, 1, 1), 1, "one column: a topic leads to its messages");
  assert.equal(focusOn(0, 2, 1), 1, "two columns: its messages already show, nothing moves");
  assert.deepEqual(chartWindow(focusOn(0, 2, 1), 2), chartWindow(0, 2));
  assert.equal(focusOn(1, 2, 2), 2, "a message's steps bring the view one column on");
  assert.deepEqual(chartWindow(2, 2), { first: 1, last: 2 });
  assert.equal(focusOn(3, 1, 1), 1, "a column to the left comes back into view");
});

test("one step sideways is one column, and none past either end", () => {
  assert.equal(stepFocus(0, 1, 1), 1);
  assert.equal(stepFocus(1, 1, -1), 0);
  assert.equal(stepFocus(0, 1, -1), null, "the first column: Back leaves the chart");
  assert.equal(stepFocus(3, 1, 1), null);
  assert.equal(stepFocus(0, 2, -1), null);
  assert.equal(stepFocus(0, 2, 1), 2);
  assert.equal(stepFocus(3, 2, -1), 2);
  assert.equal(stepFocus(0, 3, 1), 3);
  assert.equal(stepFocus(3, 3, -1), 2);
  assert.equal(stepFocus(3, 3, 1), null);
});

test("a move between columns takes a fixed short time, less for what is left of one", () => {
  assert.equal(travelMs(256, 256), 240);
  assert.equal(travelMs(-544, 272), 240, "never longer");
  assert.equal(travelMs(128, 256), 120);
  assert.equal(travelMs(10, 256), 120, "never shorter than the fast tier");
});

test("each level lays the chart out whole, so the deepest column in view sets the heights", () => {
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps: [step(NOW - 90, "bash", "x")] }), run("b", NOW - 900, topic("t1", 0))]));
  for (let level = 0; level < LEVELS; level += 1) assert.ok(node.heights[level] > 0);
  assert.ok(node.heights[1] >= node.heights[0], "a topic spans its messages once they show");
});

test("a block with a next column goes there, and any other block with something to say opens in place", () => {
  const steps = [step(NOW - 95, "bash", "ls"), step(NOW - 94, "bash", "pwd"), step(NOW - 90, "read", "a.py")];
  const live = { liveCalls: item => item.id === "b" ? [{ id: "c1", at: NOW - 80, label: "npm test" }] : [] };
  const [node] = topics(chart([run("b", NOW - 100, { ...topic("t1", 0), ended_at: null, steps }), run("a", NOW - 200, topic("t1", 0))], live));
  const [withSteps, plain] = node.kids;
  assert.equal(opensInPlace(node), false, "a topic leads to its messages");
  assert.equal(opensInPlace(withSteps), false, "a message with steps leads to them");
  assert.equal(opensInPlace(plain), true, "a message with no steps shows what was asked and replied");
  const [running, single, group] = withSteps.kids;
  assert.equal(opensInPlace(running), false, "a call still running has nothing to add yet");
  assert.equal(opensInPlace(single), true);
  assert.equal(opensInPlace(group), false);
  assert.ok(group.kids.every(opensInPlace), "each call of a group opens too");
});

test("opened details make the block taller, and every column to its left with it", () => {
  const steps = [step(NOW - 95, "bash", "ls"), step(NOW - 90, "read", "a.py"), step(NOW - 85, "bash", "pytest", { failed: true, result: "no" })];
  const roots = chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]);
  const [node] = topics(roots);
  const [message] = node.kids;
  const [failed, read] = message.kids;
  const before = { topic: node.heights[2], message: message.heights[2], read: read.heights[2], failed: failed.heights[2], overview: node.heights[0] };
  read.extra = 140;
  sizeChart(roots);
  assert.equal(read.heights[2], before.read + 140);
  assert.equal(message.heights[2], before.message + 140);
  assert.equal(node.heights[2], before.topic + 140);
  assert.equal(node.heights[0], before.overview, "a column that does not show the block keeps its height");
  read.extra = 0;
  failed.extra = 90;
  sizeChart(roots);
  assert.equal(read.heights[2], before.read);
  assert.equal(failed.heights[2], before.failed - 38 + 90, "the details stand in for the error's two lines");
});

test("a group makes room for a call opened inside it", () => {
  const steps = Array.from({ length: 22 }, (_, index) => step(NOW - 90 + index, "bash", `cmd ${index}`));
  const roots = chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]);
  const [group] = topics(roots)[0].kids[0].kids;
  const capped = group.heights[3];
  group.kids[0].extra = 200;
  sizeChart(roots);
  assert.equal(group.heights[3], capped + 200);
});

test("a call's input reads as what it was given, not as JSON", () => {
  assert.equal(readableInput({ command: "cp a b" }), "cp a b");
  assert.equal(readableInput({ path: "a.py", limit: 20, offset: null }), "path: a.py\nlimit: 20");
  assert.equal(readableInput({ edits: [{ old: "a" }] }), '[{"old":"a"}]');
  assert.equal(readableInput("plain"), "plain");
  assert.equal(readableInput({}), "");
  assert.equal(readableInput(null), "");
});

// The conversation's chart: topics, then every agent's messages in each, titled by their tasks.
const workOptions = (tasks = {}, extra = {}) => ({ text, now: NOW, waitOf: () => null, hasActions: () => false, liveCalls: () => [], reportName: () => null, nextWake: null, earlier: 0, tasks: new Map(Object.entries(tasks)), ...extra });
const work = (agents, tasks = {}, extra = {}) => buildWorkChart(agents, workOptions(tasks, extra));
const agent = (key, runs, extra = {}) => ({ key, name: `Agent ${key}`, color: null, current: false, runs, ...extra });
const forTask = (task, at, name = "bash") => step(at, name, "x", { task });
const helper = (at, name, extra = {}) => ({ kind: "subagent", at, ended_at: at + 20, job_id: `job_${name}`, name, status: "completed", never_started: false, failed: false, brief: `Brief of ${name}`, error: null, conversation: null, children: [], steps: [step(at + 5, "read", "a.py", { name: "read" })], ...extra });
const wearers = rows => { const found = []; for (const row of rows) forEachNode(row, node => { if (node.agent) found.push(node); }); return found; };

test("a topic is one row, and each message in it is titled by its task, with what was asked under it and its time last", () => {
  const runs = [
    run("c", NOW - 100, { ...topic("t1", 0), steps: [forTask("T1", NOW - 95), step(NOW - 90, "read", "notes.md", { name: "read" })] }),
    run("b", NOW - 200, { ...topic("t1", 0), asked_preview: "check them", steps: [forTask("T1", NOW - 195), forTask("T2", NOW - 190)] }),
    run("a", NOW - 300, { ...topic("t1", 0), message_status: "cancelled", reply: "" }),
  ];
  const rows = topics(work([agent("main", runs, { current: true })], { T1: { title: "Write the report", done: 1, total: 2 }, T2: { title: "Check the numbers", done: 2, total: 2 } }));
  assert.deepEqual(rows.map(row => [row.title, row.line]), [["Topic t1", "3 messages · 2 tasks"]]);
  const [row] = rows;
  // A run is one message, whole: titled by the task it worked on last.
  assert.deepEqual(row.kids.map(message => [message.role, message.depth, message.title, message.words, message.line]), [
    [1, 1, "Write the report", "asked c", "30s · 1 command"],
    [1, 1, "Check the numbers", "check them", "30s · 2 commands"],
    // No task: no title line, only what was asked and how it went.
    [1, 1, undefined, "asked a", "Stopped by you · 30s"],
  ]);
  // A title makes the block a line taller.
  assert.equal(row.kids[0].heights[1] - row.kids[2].heights[1], 21);
  const ids = [];
  forEachNode(row, node => ids.push(node.id));
  assert.equal(new Set(ids).size, ids.length);
});

test("topics keep their own colours, and from the messages on a bar is the agent's", () => {
  const runs = [run("b", NOW - 100, { ...topic("t1", 0), steps: [forTask("T1", NOW - 95)] }), run("a", NOW - 200, { ...topic("t2", 1), steps: [forTask("T2", NOW - 195)] })];
  const [newer, older] = topics(work([agent("main", runs, { current: true, color: "var(--lane-0)" })]));
  assert.deepEqual([newer.title, older.title], ["Topic t1", "Topic t2"]);
  assert.notEqual(barColor(newer), barColor(older));
  assert.equal(newer.tint, undefined);
  assert.deepEqual([barColor(newer.kids[0]), barColor(older.kids[0])], ["var(--lane-0)", "var(--lane-0)"]);
  // What the agent did inside a message is its colour, quieter.
  assert.match(barColor(newer.kids[0].kids[0]), /^color-mix\(in oklab, var\(--lane-0\) /);
  // A conversation with one branch has the main agent's own colour.
  const [row] = topics(work([agent("main", runs, { current: true })]));
  assert.equal(barColor(row.kids[0]), MAIN_COLOR);
});

test("each agent's glyph is on one block: a main agent's newest message", () => {
  const runs = [
    run("c", NOW - 100, { ...topic("t1", 0), steps: [forTask("T1", NOW - 95), step(NOW - 90, "read", "notes.md", { name: "read" })] }),
    run("b", NOW - 200, { ...topic("t1", 0), steps: [forTask("T1", NOW - 195)] }),
  ];
  const rows = topics(work([agent("main", runs, { current: true })]));
  const worn = wearers(rows);
  assert.equal(worn.length, 1);
  assert.equal(worn[0].run.id, "c");
  assert.deepEqual(worn[0].agent, { key: "main", name: "Agent main", color: MAIN_COLOR, sub: false });
});

test("a running message is live in its topic, and names the sub-agent it waits for", () => {
  const options = workOptions({}, { hasActions: () => true });
  const going = run("a", NOW - 100, { ...topic("t1", 0), ended_at: null, execution_status: "running", message_status: null,
    steps: [forTask("A", NOW - 90), helper(NOW - 20, "Scout", { task: "B", ended_at: null })] });
  const [row] = topics(buildWorkChart([agent("main", [going], { current: true })], options));
  assert.deepEqual([row.title, row.strong], ["Topic t1", true]);
  assert.match(row.line, /^Running · 1 message$/);
  const [message] = row.kids;
  // Titled by the task it is on now: the one the sub-agent was started for.
  assert.equal(message.title, "B");
  assert.equal(message.hasActions, false);
  assert.match(message.line, /^Running 1 sub-agent · /);
  // The whole run is one message: the call it made first and the sub-agent it started.
  assert.equal(message.kids.length, 2);
  assert.deepEqual([message.kids[0].title, message.kids[0].agent.sub, message.kids[0].line, message.kids[0].live], ["Scout", true, "20s", true]);
});

test("a sub-agent is a step of the message that started it, in a colour of its own, and what it did is one column on", () => {
  const runs = [run("a", NOW - 100, { ...topic("t1", 0), steps: [forTask("T1", NOW - 95), helper(NOW - 90, "Scout", { task: "T1" })] })];
  const [row] = topics(work([agent("main", runs, { current: true })], { T1: { title: "Find the bug", done: 0, total: 1 } }));
  assert.equal(row.line, "1 message · 1 task");
  const [message] = row.kids;
  // Newest first: the sub-agent was started after the call.
  const [scout, call] = message.kids;
  assert.deepEqual([scout.title, scout.line, scout.depth, scout.role], ["Scout", "20s", 2, 2]);
  assert.deepEqual(scout.agent, { key: "job_Scout", name: "Scout", color: scout.tint, sub: true });
  assert.notEqual(scout.tint, message.tint);
  assert.equal(call.tint, message.tint);
  // Its own row wears the colour whole; what it did wears it quieter, where a step's details are.
  assert.equal(barColor(scout), scout.tint);
  assert.deepEqual(scout.kids.map(kid => [kid.depth, kid.tint]), [[3, scout.tint]]);
  assert.match(barColor(scout.kids[0]), /^color-mix/);
  assert.equal(wearers([row]).length, 2);
});

test("sub-agents of one starter wear different colours, none the starter's, and keep them as more are started", () => {
  const names = ["Scout", "Writer", "Reviewer", "Planner", "Checker"];
  const steps = names.map((name, index) => helper(NOW - 90 + index * 2, name));
  const colours = given => {
    const [row] = topics(work([agent("main", [run("a", NOW - 100, { ...topic("t1", 0), steps: given })], { current: true, color: "var(--lane-2)" })]));
    return new Map(row.kids[0].kids.map(kid => [kid.title, kid.tint]));
  };
  const all = colours(steps);
  assert.equal(new Set(all.values()).size, names.length);
  assert.ok(![...all.values()].includes("var(--lane-2)"));
  const fewer = colours(steps.slice(0, 3));
  for (const [name, colour] of fewer) assert.equal(all.get(name), colour);
});

test("a sub-agent keeps its colour when a branch is added, and one main agent's sub-agents differ across its messages", () => {
  const names = ["Scout", "Writer", "Reviewer", "Planner", "Checker", "Editor"];
  // One sub-agent in each of six messages of the same main agent.
  const runs = names.map((name, index) => run(`r${index}`, NOW - 100 - index * 50, { ...topic("t1", 0), steps: [helper(NOW - 95 - index * 50, name)] }));
  const colours = agents => {
    const found = new Map();
    for (const row of topics(work(agents))) forEachNode(row, node => { if (node.agent?.sub) found.set(node.title, node.tint); });
    return found;
  };
  const alone = colours([agent("main", runs, { current: true })]);
  assert.equal(new Set(alone.values()).size, names.length);
  // A second branch wearing one of the sub-agent colours moves at most the sub-agent that wore it.
  const taken = alone.get("Scout");
  const forked = colours([agent("main", runs, { current: true, color: "var(--lane-0)" }), agent("other", [run("x", NOW - 10, topic("t1", 0)), ...runs], { color: taken })]);
  const moved = names.filter(name => forked.get(name) !== alone.get(name));
  assert.ok(moved.length <= 2, `moved: ${moved}`);
  assert.ok(![...forked.values()].includes(taken));
  // Checking out the other branch changes no colour.
  const switched = colours([agent("main", runs, { color: "var(--lane-0)" }), agent("other", [run("x", NOW - 10, topic("t1", 0)), ...runs], { current: true, color: taken })]);
  assert.deepEqual([...switched].sort(), [...forked].sort());
});

test("a sub-agent's own sub-agent is a step of its work, and what that one did adds a column", () => {
  const inner = helper(NOW - 80, "Explore", { steps: [step(NOW - 78, "read", "b.py", { name: "read" }), step(NOW - 76, "bash", "ls")] });
  const outer = helper(NOW - 90, "Scout", { children: [inner], ended_at: NOW - 60 });
  const roots = work([agent("main", [run("a", NOW - 100, { ...topic("t1", 0), steps: [outer] })], { current: true })]);
  assert.equal(levelsOf(roots), LEVELS + 1);
  const [row] = topics(roots);
  const [scout] = row.kids[0].kids;
  const explore = scout.kids.find(kid => kid.agent);
  assert.deepEqual([scout.depth, explore.depth, explore.kids[0].depth], [2, 3, 4]);
  assert.notEqual(explore.tint, scout.tint);
  assert.equal(explore.kids[0].tint, explore.tint);
  // A step whose rows are all single calls scrolls in its own cell; one with more under a row does not.
  assert.deepEqual([scout.folds, explore.folds], [false, true]);
  forEachNode(row, node => assert.equal(node.heights.length, LEVELS + 1));
  // Laid out at the last column, the sub-agent is as tall as what it did.
  assert.ok(scout.heights[4] >= scout.kids.reduce((sum, kid) => sum + kid.heights[4], 0) - 0.01);
  // An Agent's own chart stops at its fourth column.
  const [own] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps: [outer] })]));
  assert.equal(levelsOf([{ kind: "topic", id: own.id, node: own }]), LEVELS);
});

test("messages of two branches share a topic's column by time, each in its branch's colour", () => {
  const shared = run("a", NOW - 300, { ...topic("t1", 0), steps: [forTask("T1", NOW - 295)] });
  const mine = run("b", NOW - 100, { ...topic("t1", 0), steps: [forTask("T1", NOW - 95)] });
  const theirs = run("c", NOW - 200, { ...topic("t1", 0), steps: [forTask("T1", NOW - 195)] });
  const [row] = topics(work([agent("other", [theirs, shared], { color: "var(--lane-1)" }), agent("main", [mine, shared], { current: true, color: "var(--lane-0)" })], { T1: { title: "Task", done: 0, total: 0 } }));
  // A run two branches share is the checked-out branch's.
  assert.deepEqual(row.kids.map(message => [message.run.id, message.tint]), [["b", "var(--lane-0)"], ["c", "var(--lane-1)"], ["a", "var(--lane-0)"]]);
  assert.deepEqual(row.kids.map(message => message.agent?.name), ["Agent main", "Agent other", undefined]);
  assert.deepEqual(row.kids.map(message => message.title), ["Task", "Task", "Task"]);
  assert.equal(row.line, "3 messages · 1 task");
});

test("a main agent whose sub-agents can be stopped offers it on the block with its glyph, which is a line taller", () => {
  const runs = [run("a", NOW - 100, { ...topic("t1", 0), steps: [helper(NOW - 90, "Scout", { ended_at: null, task: "T" })] })];
  const tasks = { T: { title: "Scout the code", done: 0, total: 1 } };
  const [plain] = topics(work([agent("main", runs, { current: true })], tasks));
  const [stops] = topics(work([agent("main", runs, { current: true })], tasks, { stoppable: key => key === "main" }));
  assert.deepEqual([Boolean(plain.kids[0].stops), stops.kids[0].stops], [false, true]);
  assert.equal(stops.kids[0].heights[1] - plain.kids[0].heights[1], 28);
});

test("the conversation's chart has four columns, and its blocks have a height at every level", () => {
  const runs = [run("a", NOW - 100, { ...topic("t1", 0), steps: [forTask("T1", NOW - 95), forTask("T1", NOW - 90), forTask("T1", NOW - 85, "read")] })];
  const roots = work([agent("main", runs, { current: true })]);
  assert.equal(levelsOf(roots), LEVELS);
  const [row] = topics(roots);
  assert.deepEqual([row.title, row.kids[0].title], ["Topic t1", "T1"]);
  forEachNode(row, node => assert.equal(node.heights.length, LEVELS));
  // Two calls of one function share a line; each call is one column further.
  const group = row.kids[0].kids.find(kid => kid.kids);
  assert.deepEqual([group.role, group.depth, group.kids[0].depth, group.folds], [2, 2, 3, true]);
  assert.deepEqual(chartWindow(4, 3, 5), { first: 2, last: 4 });
  assert.equal(stepFocus(2, 3, 1, 5), 3);
  assert.equal(columnsFor(COLUMN_WIDTH * 6, 5), 3);
});
