// The task block a reply draws for a stretch of work the agent named
// (components/chat/messages/execution/task-block.tsx): its header in each
// state, the outline, a step's work, and the reader's own open and fold.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../../../_support/load.mjs";
import { installDom, render } from "../../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/components/solar-icons": `import { createElement } from "react";
      export const SolarIcon = ({ name }) => createElement("i", { "data-glyph": name });`,
  },
});
installDom({ pathname: "/chat" });
const { createElement: h } = await import("react");
const { TaskBlock } = await import("../../../../components/chat/messages/execution/task-block.tsx");
const { drawnState, layoutReply, normalizeSegments } = await import("../../../../lib/chat/transcript/task-blocks.ts");
const { revealTask } = await import("../../../../lib/chat/transcript/use-row-tasks.ts");
const { getTaskOpen, reloadTaskOpen, setTaskOpen, stepOpenKey, taskOpenKey } = await import("../../../../lib/prefs/task-block-open.ts");

let view;
afterEach(async () => {
  await view?.unmount();
  view = undefined;
  localStorage.removeItem("op.taskBlockOpen.v1");
  reloadTaskOpen();
});
const $ = (selector) => view.container.querySelector(selector);
const $$ = (selector) => [...view.container.querySelectorAll(selector)];
const texts = (selector) => $$(selector).map((node) => node.textContent);

const steps = (...statuses) => statuses.map((status, at) => ({
  id: String(14 + at), subject: `Step ${at + 1}`, status, ...(status === "completed" ? { result: `did ${at + 1}` } : {}),
}));
const raw = (fields = {}) => ({
  anchor_id: "r1", plan_id: "T3", scope: "", title: "Fold the agent list", purpose: "Finished agents fold into one row.",
  state: "running", outcome: "", advanced: true, current: "16", earlier: [],
  started_at: 100, ended_at: null, created_at: 100, updated_at: 251,
  todos: steps("completed", "completed", "in_progress", "pending", "pending"), ...fields,
});
const stamp = (step) => ({ task: "T3", step });
const WORK = [
  { type: "tool", tool: "task_start", ...stamp("14") },
  { type: "tool", tool: "grep", ...stamp("14") },
  { type: "text", text: "The list is in agent-list.tsx.", ...stamp("14") },
  { type: "tool", tool: "edit", ...stamp("15") },
  { type: "tool", tool: "read", ...stamp("16") },
  { type: "tool", tool: "bash", ...stamp("16") },
];
const summary = { label: "Ran a search, edited a file", failed: 0, added: 42, removed: 7 };

function block(fields = {}, { status = "streaming", blocks = WORK, ...props } = {}) {
  const data = raw(fields);
  // A stored todo list is an entry with no title key at all.
  if ("title" in fields && fields.title === undefined) delete data.title;
  const [segment] = normalizeSegments([data]);
  const tasks = segment.legacy ? { segments: [], legacy: [segment] } : { segments: [segment], legacy: [] };
  const part = layoutReply(blocks, blocks.map((_, at) => `b${at}`), tasks).find((item) => item.kind === "task");
  return h(TaskBlock, {
    rowId: "r1", part, drawn: drawnState(segment, status), last: true,
    renderItem: ({ b, i }) => h("div", { key: i, className: "work" }, b.tool ?? b.text),
    summarize: (items) => ({ ...summary, failed: items.some(({ b }) => b.is_error) ? 1 : 0 }),
    livePhrase: () => "running a command",
    ...props,
  });
}
async function draw(fields, options) {
  view = await render(block(fields, options));
}
const opened = () => $$(".tb-step").map((step) => step.getAttribute("data-open"));
const key = (target, name) => view.act(() => {
  const event = new Event("keydown", { bubbles: true, cancelable: true });
  Object.assign(event, { key: name });
  target.dispatchEvent(event);
});

describe("task block header", () => {
  test("running: open to the outline, progress in the header, every step folded to its line", async () => {
    await draw();
    assert.deepEqual([$(".tb").getAttribute("data-state"), $(".tb").getAttribute("data-open")], ["running", "1"]);
    assert.deepEqual([$(".tb").getAttribute("data-task-id"), $(".tb").getAttribute("data-anchor")], ["T3", "r1"]);
    assert.equal($(".tb-head").getAttribute("aria-expanded"), "true");
    assert.ok($(".tb-name").classList.contains("tl-label-shimmer"));
    assert.equal($(".tb-name").textContent, "Fold the agent list");
    assert.equal($(".tb-cur").textContent, "· Step 3");
    assert.equal(texts(".tb-meta")[0], "3/5", "running: the step it is on");
    assert.equal($(".tb-head .turn-files-diff").textContent, "+42−7");
    assert.equal($(".tb-head .tl-stopped"), null);
    assert.equal($(".tb-purpose").textContent, "Finished agents fold into one row.");
    assert.deepEqual($$(".tb-step").map((step) => step.getAttribute("data-state")), ["done", "done", "run", "todo", "todo"]);
    // The steps the agent set, and nothing of the work until the reader asks.
    assert.deepEqual(opened(), ["0", "0", "0", "0", "0"]);
    assert.equal($(".tb-step[data-state='run'] .tb-step-head").getAttribute("aria-expanded"), "false");
    assert.equal($(".tb-step[data-open='1']"), null);
    assert.deepEqual(texts(".tb-step-note").slice(0, 3), ["did 1", "did 2", "running a command"]);
    // A step with no work is a plain row: nothing to open, not in the tab order.
    const waiting = $$(".tb-step-head")[3];
    assert.ok(waiting.classList.contains("is-static"));
    assert.equal(waiting.hasAttribute("tabindex"), false);
    assert.equal(waiting.querySelector(".tb-step-chev"), null);
    assert.equal(waiting.querySelector(".sr-only").textContent, "To do: ");
  });

  test("finished: one line with the written result, the step count and the time", async () => {
    await draw({ state: "done", outcome: "finished agents fold into one row", current: null, ended_at: 340,
      todos: steps("completed", "completed", "completed", "completed", "completed") }, { status: "done" });
    assert.deepEqual([$(".tb").getAttribute("data-state"), $(".tb").getAttribute("data-open")], ["done", "0"]);
    assert.equal($(".tb-out").textContent, "— finished agents fold into one row");
    assert.deepEqual(texts(".tb-meta"), ["5 steps", "4m 0s"]);
    assert.equal($(".tb-cur"), null);
    assert.equal($(".tb-body"), null, "a folded block mounts no outline");
    assert.ok($(".tb-ico").classList.contains("tb-checked"));
  });

  test("finished with no written result: the deterministic summary of its calls", async () => {
    await draw({ state: "done", ended_at: 130, todos: steps("completed") }, { status: "done" });
    assert.equal($(".tb-out").textContent, "— Ran a search, edited a file");
    assert.deepEqual(texts(".tb-meta"), ["1 step", "30s"]);
  });

  test("stopped: stays open with the Stopped tag on the step it stopped on; waiting steps stay waiting", async () => {
    await draw({}, { status: "cancelled", endedAt: 251 });
    assert.deepEqual([$(".tb").getAttribute("data-state"), $(".tb").getAttribute("data-open")], ["stopped", "1"]);
    assert.equal($(".tb-head .tl-stopped").textContent, "Stopped");
    assert.equal($(".tb-name").classList.contains("tl-label-shimmer"), false);
    assert.deepEqual($$(".tb-step").map((step) => step.getAttribute("data-state")), ["done", "done", "held", "todo", "todo"]);
    assert.equal(texts(".tb-step-note")[2], "stopped here");
    assert.deepEqual(texts(".tb-meta"), ["2/5", "2m 31s"]);
  });

  test("the block's rails have a colour outside a timeline, and the name outlasts the result", async () => {
    const { readFile } = await import("node:fs/promises");
    const sheet = (name) => readFile(new URL(`../../../../app/styles/chat/execution/${name}`, import.meta.url), "utf8");
    // A block is not inside .tl, so the rail colour has to reach .tb too.
    const strip = await sheet("execution-strip.css");
    assert.match(strip, /\.tl\s*\{[^}]*--timeline-line:/);
    assert.match(strip, /\.tb\s*\{[^}]*--timeline-line:/);
    const css = await sheet("task-block.css");
    const shrink = (selector) => Number(new RegExp(`${selector}\\s*\\{[^}]*flex:\\s*0\\s+(\\d+)\\s+auto`).exec(css)?.[1]);
    assert.ok(shrink("\\.tb-out,\\s*\\.tb-cur") >= 1000 * shrink("\\.tb-name"));
    assert.match(css, /@container[^{]*\{\s*\.tb-head > \.tb-time,\s*\.tb-head > \.tl-diff\s*\{\s*display:\s*none/);
  });

  test("a stopped block's clock ends when the reply did, and is left out when that is not known", async () => {
    // The segment was last written at 251; the reply went on until 400.
    await draw({}, { status: "cancelled", endedAt: 400 });
    assert.deepEqual(texts(".tb-meta"), ["2/5", "5m 0s"]);
    // The time is marked, so a narrow column can drop it and keep the progress.
    assert.deepEqual(texts(".tb-time"), ["5m 0s"]);
    await view.unmount();
    await draw({}, { status: "cancelled" });
    assert.deepEqual(texts(".tb-meta"), ["2/5"]);
  });

  test("a reply waiting on the person keeps its block running, without motion or a tag", async () => {
    await draw({}, { status: "paused", active: false });
    assert.deepEqual([$(".tb").getAttribute("data-state"), $(".tb").getAttribute("data-open")], ["running", "1"]);
    assert.equal($(".tb-head .tl-stopped"), null);
    assert.equal($(".tb-name").classList.contains("tl-label-shimmer"), false);
    assert.equal($$(".tb-step")[2].getAttribute("data-open"), "0");
    assert.equal(texts(".tb-step-note")[2], "");
  });

  test("interrupted and failed carry their own tags; a failed block marks the step red", async () => {
    await draw({}, { status: "interrupted" });
    assert.equal($(".tb-head .tl-stopped").textContent, "Interrupted");
    await view.unmount();
    await draw({}, { status: "error", blocks: [...WORK.slice(0, 5), { ...WORK[5], is_error: true }] });
    assert.equal($(".tb").getAttribute("data-state"), "failed");
    assert.ok($(".tb-head .tl-stopped").classList.contains("is-fail"));
    assert.equal($(".tb-head .tl-stopped").textContent, "Failed");
    assert.equal($$(".tb-step")[2].getAttribute("data-state"), "fail");
    assert.equal(texts(".tb-step-note")[2], "1 failed");
  });

  test("displaced by a second task: folded, reading where it stopped, never a check", async () => {
    await draw({ state: "displaced", current: "17", ended_at: 280,
      todos: steps("completed", "completed", "completed", "in_progress", "pending") }, { status: "done", last: false });
    assert.equal($(".tb").getAttribute("data-open"), "0");
    assert.equal($(".tb-out").textContent, "— stopped at 3/5");
    assert.deepEqual(texts(".tb-meta"), ["3m 0s"]);
    assert.equal($(".tb-ico").classList.contains("tb-checked"), false);
  });

  test("a Goal turn's segment: earlier steps are one row that leads back, the line says what this turn did", async () => {
    await draw({
      state: "carried", earlier: ["14", "15"], current: null, ended_at: 400,
      todos: steps("completed", "completed", "completed", "completed", "pending"),
    }, { status: "done", blocks: [{ type: "tool", tool: "bash", ...stamp("16") }], earlierAnchor: "r0" });
    assert.equal($(".tb-out").textContent, "— steps 3–4 this turn");
    assert.equal(texts(".tb-meta")[0], "4/5");
    await view.act(() => $(".tb-head").click());
    assert.equal($(".tb-jump .tb-step-title").textContent, "2 earlier steps done");
    assert.equal($(".tb-jump .tb-step-head").getAttribute("role"), "button");
    assert.deepEqual(texts(".tb-step:not(.tb-jump) .tb-step-title"), ["Step 3", "Step 4", "Step 5"]);
  });

  test("the jump row is plain text when the earlier reply is not loaded", async () => {
    await draw({ earlier: ["14", "15"] });
    assert.ok($(".tb-jump .tb-step-head").classList.contains("is-static"));
    assert.equal($(".tb-jump .tb-step-head").hasAttribute("role"), false);
  });

  test("a task just started is on its first step, with its work under that step", async () => {
    await draw({ advanced: false, current: "14", todos: steps("in_progress", "pending") },
      { blocks: WORK.map((block) => ({ ...block, step: "14" })) });
    assert.equal(texts(".tb-meta")[0], "1/2");
    await view.act(() => $(".tb-step-head").click());
    assert.deepEqual(texts(".tb-step[data-open='1'] .tl-sub .work"), ["grep", "The list is in agent-list.tsx.", "edit", "read", "bash"]);
  });

  test("a task that ended without advancing shows no progress and lays its work flat under the outline", async () => {
    await draw({ advanced: false, state: "done", ended_at: 340, todos: steps("in_progress", "pending") }, { status: "done" });
    assert.deepEqual(texts(".tb-meta").filter((item) => /\d\/\d|step/.test(item)), []);
    await view.act(() => $(".tb-head").click());
    assert.deepEqual(texts(".tb-flat .work"), ["grep", "The list is in agent-list.tsx.", "edit", "read", "bash"]);
    assert.ok($$(".tb-step-head").every((head) => head.classList.contains("is-static")));
  });

  test("a stored todo list is one folded block named Todos with its count", async () => {
    await draw({ title: undefined }, { status: "done", blocks: [{ type: "tool", tool: "todo_update" }] });
    const head = $(".tb-head");
    assert.equal($(".tb").getAttribute("data-open"), "0");
    assert.equal($(".tb-name").textContent, "Todos");
    assert.deepEqual(texts(".tb-meta"), ["2/5"]);
    assert.equal(head.querySelector(".tb-out"), null);
    await view.act(() => head.click());
    assert.equal($$(".tb-step").length, 5);
    assert.equal($(".tl-sub"), null, "a stored list has an outline only");
  });
});

describe("opening and folding", () => {
  test("the reader's own fold wins over the running block and is kept for the reply", async () => {
    await draw();
    await view.act(() => $(".tb-head").click());
    assert.equal($(".tb").getAttribute("data-open"), "0");
    assert.equal(getTaskOpen("r1", taskOpenKey("T3")), false);
    assert.match(localStorage.getItem("op.taskBlockOpen.v1"), /task:T3/);
    await view.unmount();
    reloadTaskOpen();
    await draw();
    assert.equal($(".tb").getAttribute("data-open"), "0", "a reload keeps the choice");
    assert.equal($(".tb-head").getAttribute("aria-expanded"), "false");
  });

  test("a finished block opened by hand shows the outline, and a step opens to its work", async () => {
    await draw({ state: "done", ended_at: 340, todos: steps("completed", "completed", "completed", "completed", "completed") }, { status: "done" });
    await view.act(() => $(".tb-head").click());
    assert.equal($(".tb").getAttribute("data-open"), "1");
    const first = $$(".tb-step")[0];
    assert.equal(first.getAttribute("data-open"), "0");
    assert.equal(first.querySelector(".tb-step-head").getAttribute("aria-expanded"), "false");
    await view.act(() => first.querySelector(".tb-step-head").click());
    assert.equal($$(".tb-step")[0].getAttribute("data-open"), "1");
    assert.deepEqual(texts(".tb-step[data-open='1'] .work"), ["grep", "The list is in agent-list.tsx."]);
    assert.equal(getTaskOpen("r1", stepOpenKey("T3", "14")), true);
  });

  test("arrow keys fold and open like the Activity roster; Enter and Space toggle a step", async () => {
    await draw();
    await key($(".tb-head"), "ArrowRight");
    assert.equal($(".tb").getAttribute("data-open"), "1", "already open: ArrowRight does nothing");
    await key($(".tb-head"), "ArrowLeft");
    assert.equal($(".tb").getAttribute("data-open"), "0");
    await key($(".tb-head"), "ArrowRight");
    assert.equal($(".tb").getAttribute("data-open"), "1");
    const current = () => $$(".tb-step")[2];
    assert.equal(current().querySelector(".tb-step-head").getAttribute("tabindex"), "0");
    await key(current().querySelector(".tb-step-head"), "Enter");
    assert.equal(current().getAttribute("data-open"), "1");
    await key(current().querySelector(".tb-step-head"), " ");
    assert.equal(current().getAttribute("data-open"), "0");
    await key(current().querySelector(".tb-step-head"), "ArrowRight");
    assert.equal(current().getAttribute("data-open"), "1");
    await key(current().querySelector(".tb-step-head"), "ArrowLeft");
    assert.equal(current().getAttribute("data-open"), "0");
  });

  test("a step opens and folds only by the reader's click, and keeps that through new work and its finish", async () => {
    await draw();
    const step = () => $$(".tb-step")[2];
    await view.act(() => step().querySelector(".tb-step-head").click());
    assert.deepEqual(opened(), ["0", "0", "1", "0", "0"]);
    assert.deepEqual(texts(".tb-step[data-open='1'] .tl-sub .work"), ["read", "bash"]);
    // More work streams in under the step, then the step finishes and the next starts.
    const more = [...WORK, { type: "tool", tool: "grep", ...stamp("16") }];
    await view.rerender(block({}, { blocks: more }));
    assert.deepEqual(opened(), ["0", "0", "1", "0", "0"]);
    await view.rerender(block({ current: "17", todos: steps("completed", "completed", "completed", "in_progress", "pending") },
      { blocks: [...more, { type: "tool", tool: "edit", ...stamp("17") }] }));
    assert.deepEqual(opened(), ["0", "0", "1", "0", "0"], "the finished step stays open; the new current step does not open itself");
    assert.deepEqual(texts(".tb-step[data-open='1'] .tl-sub .work"), ["read", "bash", "grep"]);
    await view.act(() => step().querySelector(".tb-step-head").click());
    assert.deepEqual(opened(), ["0", "0", "0", "0", "0"]);
  });

  test("a block whose sub-agent still runs stays open; a choice stored for another reply does not apply", async () => {
    setTaskOpen("other-reply", taskOpenKey("T3"), false);
    await draw({ state: "done", ended_at: 340 }, { status: "done", agentsRunning: true });
    assert.equal($(".tb").getAttribute("data-open"), "1");
  });

  test("revealing a block that is already open records no choice; a folded one is opened", async () => {
    const scroll = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = () => {};
    try {
      await draw();
      // The chip that calls this sits outside the scroll area: the seek is
      // announced, so the area does not pull the view back to the latest.
      const heard = [];
      view.container.addEventListener("chat-reader-seek", (event) => heard.push(event.target.className));
      assert.equal(revealTask("T3", "r1", view.container), true);
      assert.deepEqual(heard, ["tb"]);
      assert.equal(getTaskOpen("r1", taskOpenKey("T3")), null, "the block still folds by itself when the task ends");
      await view.act(() => $(".tb-head").click());
      assert.equal($(".tb").getAttribute("data-open"), "0");
      await view.act(() => { revealTask("T3", "r1", view.container); });
      assert.equal(getTaskOpen("r1", taskOpenKey("T3")), true);
      assert.equal($(".tb").getAttribute("data-open"), "1");
      assert.equal(revealTask("T9", "r9", view.container), false);
    } finally {
      window.HTMLElement.prototype.scrollIntoView = scroll;
    }
  });

  test("only the 500 most recent choices are kept", () => {
    for (let at = 0; at < 505; at++) setTaskOpen(`row${at}`, taskOpenKey("T1"), true);
    assert.equal(getTaskOpen("row0", taskOpenKey("T1")), null);
    assert.equal(getTaskOpen("row504", taskOpenKey("T1")), true);
    assert.equal(JSON.parse(localStorage.getItem("op.taskBlockOpen.v1")).length, 500);
  });
});

test("the levels are one indent apart, and size and weight do not mark them", () => {
  const css = readFileSync(new URL("../../../../app/styles/chat/execution/task-block.css", import.meta.url), "utf8");
  const rule = (selector) => {
    const at = css.indexOf(`\n${selector} {`);
    assert.ok(at >= 0, selector);
    return css.slice(at, css.indexOf("}", at));
  };
  // The purpose under the name, the steps one indent in, a step's work one more.
  for (const selector of [".tb-body", ".tb-steps", ".tb .tl-sub"]) assert.match(rule(selector), /padding-left: var\(--tb-indent\)/);
  for (const selector of [".tb-step-ico", ".tb .tl-sub .tl-step-icon"]) {
    assert.match(rule(selector), /left: calc\(var\(--tb-pill\) - var\(--tb-indent\)\)/);
  }
  // One rail, the steps': a step's work draws none of its own.
  assert.match(rule(".tb .tl-sub::before"), /display: none/);
  // Neither the name nor a step, in any state, is set apart by weight or size.
  for (const selector of [".tb-name", ".tb-step-title"]) assert.doesNotMatch(rule(selector), /font-weight/);
  assert.doesNotMatch(css.match(/\.tb-step\[data-state[^{]*\{[^}]*\}/g).join("\n"), /font-weight|font-size/);
});

test("the block is drawn on the rail with theme tokens, without a frame", () => {
  const css = readFileSync(new URL("../../../../app/styles/chat/execution/task-block.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b|rgba?\(|hsl\(/i);
  assert.doesNotMatch(css.match(/\.tb \{[^}]*\}/)[0], /border/);
  assert.doesNotMatch(css.match(/\.tb-head \{[^}]*\}/)[0], /border:\s*1px|box-shadow/);
  const sheet = readFileSync(new URL("../../../../app/styles/index.css", import.meta.url), "utf8");
  assert.ok(sheet.indexOf("execution-strip.css") < sheet.indexOf("task-block.css"));
  assert.doesNotMatch(readFileSync(new URL("../../../../components/chat/messages/execution/task-block.tsx", import.meta.url), "utf8"), /HoverTip|Tooltip|title=/);
});

test("the reply bubble draws task blocks, and nothing draws todo cards", () => {
  const bubble = readFileSync(new URL("../../../../components/chat/messages/bubbles/assistant-bubble.tsx", import.meta.url), "utf8");
  assert.match(bubble, /layoutReply\(effBlocks, blockKeys, tasks\)/);
  assert.match(bubble, /<TaskBlock/);
  for (const file of ["components/chat/messages/list/transcript-rows.tsx", "components/chat/messages/list/rows/message-rows.tsx"]) {
    assert.doesNotMatch(readFileSync(new URL(`../../../../${file}`, import.meta.url), "utf8"), /TodoPlans|todo-plan/);
  }
});
