// What an Agent's time chart draws, without a page around it.
import assert from "node:assert/strict";
import test from "node:test";
import { buildChart, chartWindow, columnsFor, focusOn, forEachNode, opensInPlace, paintColor, panelWidthFor, readableInput, sizeChart, stepFocus, travelMs, COLUMN_WIDTH, LEVELS, PANEL_INSET, PANEL_SNAP_WIDTHS } from "../../components/right-sidebar/running/agent-chart-model.ts";

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

test("a step that failed mid-run leaves a finished message plain", () => {
  const steps = [step(NOW - 95, "bash", "pytest", { failed: true, result: "no" }), step(NOW - 90, "bash", "pytest")];
  const [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), steps })]));
  assert.match(node.kids[0].line, /^30s · 1 step failed$/);
  assert.equal(node.kids[0].strong, false);
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

test("the question asked titles a message and the time is its latest moment", () => {
  const [node] = topics(chart([run("a", NOW - 100, topic("t1", 0))]));
  assert.equal(node.kids[0].title, "asked a");
  assert.equal(node.time, node.kids[0].time);
  assert.equal(node.kids[0].time, new Date((NOW - 70) * 1000).toTimeString().slice(0, 5));
});

test("an unsorted message joins the one before it, and with none before it waits", () => {
  let [node] = topics(chart([run("b", NOW - 100), run("a", NOW - 200, topic("t1", 0))]));
  assert.equal(node.title, "Topic t1");
  assert.equal(node.kids.length, 2);
  [node] = topics(chart([run("a", NOW - 200)]));
  assert.equal(node.title, "Not sorted yet");
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

test("running and waiting say what they are doing, and only they get actions", () => {
  const live = run("a", NOW - 125, { ...topic("t1", 0), ended_at: null, execution_id: "e", execution_status: "running", steps: [step(NOW - 60, "bash", "build")] });
  let [node] = topics(chart([live], { hasActions: () => true }));
  assert.match(node.kids[0].line, /^Running .* · 2m 5s$/);
  assert.equal(node.kids[0].hasActions, true);
  assert.match(node.line, /^Running · 1 message$/);
  [node] = topics(chart([live], { hasActions: () => true, waitOf: () => ({ waitLabel: "Waiting for your answer", question: "Overwrite it?" }) }));
  assert.equal(node.kids[0].line, "Waiting · Overwrite it?");
  assert.equal(node.line, "Waiting for your answer");
  [node] = topics(chart([run("a", NOW - 100, { ...topic("t1", 0), execution_status: "failed" })], { hasActions: () => true }));
  assert.equal(node.kids[0].hasActions, false);
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
