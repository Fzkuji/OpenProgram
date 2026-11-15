// The pages opened from Programs and commands, Open work and Scheduled
// tasks, the always-on row and a report inside the run list.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../", import.meta.url);
const stub = source => ({ url: `data:text/javascript,${encodeURIComponent(source)}`, shortCircuit: true });
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) return stub("export default new Proxy({}, { get: (_, name) => String(name) })");
    if (specifier === "@/components/agents/agent-avatar") return stub("export const AgentAvatarById = () => null; export const useConversationAgentShape = () => 'cloud'");
    if (specifier === "@/lib/nav/navigate") return stub("export const navigate = path => { globalThis.navigated = path; }");
    if (specifier === "@/lib/net/execution/process-client") return stub("export const processIsActive = p => ['starting','running','stopping','unknown'].includes(p.status); export const stopProcess = async (...a) => { globalThis.stopped = a; return {}; }");
    if (specifier === "@/lib/net/execution/activity-runs-client") return stub("export const getActivityRuns = (...a) => globalThis.activityRuns(...a); export const getActivityRunEvents = (...a) => globalThis.activityEvents(...a)");
    if (specifier === "@/lib/chat/conversation-work") return stub("const log = (name, a) => { (globalThis.workCalls ||= []).push([name, ...a]); return Promise.resolve({ ok: true }); }; export const setOpenWorkStatus = (...a) => log('status', a); export const removeOpenWork = (...a) => log('remove', a); export const wakeConversation = (...a) => log('wake', a); export const addReportPreference = (...a) => log('prefer', a); export const removeReportPreference = (...a) => log('forget', a); export const dontReportText = title => `Don't report: ${title}`");
    const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier, context.parentURL).href : null;
    if (base) for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".tsx")) return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
      compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText };
    return nextLoad(url, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
Object.assign(globalThis, { window, document: window.document, Event: window.Event, CustomEvent: window.CustomEvent, HTMLElement: window.HTMLElement, SVGElement: window.SVGElement, MutationObserver: window.MutationObserver, CSS: { escape: value => value }, IS_REACT_ACT_ENVIRONMENT: true });
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = callback => setTimeout(() => callback(0), 0);
globalThis.cancelAnimationFrame = clearTimeout;
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.HTMLElement.prototype.focus = function () { document.activeElement = this; };
window.HTMLElement.prototype.scrollTo = function () {};

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { ActivityPage } = await import("../../components/right-sidebar/running/activity-page.tsx");
const { ActivityPageFrame } = await import("../../components/right-sidebar/running/activity-page.tsx");
const { AlwaysOnRow, reportForRun, reportNameForRun, ReportInside } = await import("../../components/right-sidebar/running/always-on-row.tsx");
const { ActivityPanelReact } = await import("../../components/right-sidebar/running/activity-page-row.tsx");

const now = Math.floor(Date.now() / 1000);
const program = (extra = {}) => ({ id: "p1", session_id: "session", execution_id: "e1", command: "python  train.py --epochs 3", cwd: "/tmp/x", status: "exited", started_at: now - 100, ended_at: now - 97, pid: 1, exit_code: 0, backend_id: "local", truncated: false, can_stop: false, title: "Train it", ...extra });
const context = (overrides = {}) => ({
  sessionId: "session", text: english => english, now, active: true, connected: true,
  controller: { executions: [] }, processes: { items: [], calls: [], spawns: [], detail: null, stale: false, loaded: true, refresh() { globalThis.refreshed = true; } },
  work: { session_id: "session", always_on: { enabled: false, reports: [] }, open_work: [], routines: [] }, refreshWork() {}, scopes: [], scopeOf() {},
  scopeHeadText() {}, waitingFor: () => null, answerInChat() {}, switchTo() {}, openAgentConversation() {}, alwaysStatus: () => ({ label: "Next wake 18:00", tone: "muted" }),
  act: async run => { await run(); return true; }, pendingKey: null, actionError: null, copy(key, value) { globalThis.copied = [key, value]; }, copiedKey: null, close() { globalThis.closed = true; },
  ...overrides,
});
async function page(target, ctx, check) {
  globalThis.activityRuns ??= async () => ({ runs: [], offset: 0, total: 0 });
  globalThis.activityEvents ??= async () => ({ run_id: "x", revision: "r", events: [] });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const render = next => act(async () => root.render(createElement(ActivityPage, { target, context: next })));
  await render(ctx);
  const click = async label => {
    const button = [...host.querySelectorAll("button")].find(item => item.textContent.includes(label));
    assert.ok(button, label);
    await act(async () => button.click());
  };
  try { await check(host, click, render); } finally { await act(async () => root.unmount()); host.remove(); }
}
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
test.afterEach(() => { for (const key of ["stopped", "workCalls", "copied", "closed", "navigated", "activityRuns", "activityEvents", "chartQueries", "commands"]) delete globalThis[key]; });

test("a finished program says when, how long and how it ended, with output open", async () => {
  const ctx = context({ processes: { ...context().processes, items: [program()], detail: { process: program(), output: "hello world\n" } } });
  await page({ kind: "program", processId: "p1" }, ctx, async (host, click) => {
    assert.match(host.textContent, /Train it/);
    assert.match(host.textContent, /· 3s · exit code 0/);
    assert.doesNotMatch(host.textContent, /Stop program/);
    assert.match(host.textContent, /Command: python train\.py --epochs 3/);
    assert.match(host.textContent, /hello world/);
    await click("Technical details");
    assert.match(host.textContent, /Environment/);
    assert.match(host.textContent, /\/tmp\/x/);
    assert.match(host.textContent, /e1/);
  });
});

test("a running program can be stopped and shows Live", async () => {
  const running = program({ status: "running", ended_at: null, exit_code: null, can_stop: true, progress: { source: "marker", current: 45, total: 100, label: "samples", unit: null, state: "normal", rate: null, eta: null } });
  const ctx = context({ processes: { ...context().processes, items: [running], detail: { process: running, output: "" } } });
  await page({ kind: "program", processId: "p1" }, ctx, async (host, click) => {
    assert.match(host.textContent, /Output · Live/);
    assert.match(host.textContent, /Progress: 45 \/ 100 samples · 45%/);
    assert.ok(host.querySelector("[role=progressbar]"));
    await click("Stop program");
    assert.deepEqual(globalThis.stopped, ["p1", "session"]);
    assert.equal(globalThis.refreshed, true);
  });
});

test("a failed program is red, and one whose supervisor is gone says so", async () => {
  const failed = program({ status: "exited", exit_code: 2 });
  await page({ kind: "program", processId: "p1" }, context({ processes: { ...context().processes, items: [failed] } }), async host => {
    assert.match(host.textContent, /Exited · .* · 3s · exit code 2/);
    assert.ok(host.querySelector('[data-tone="err"]'));
  });
  const lost = program({ status: "unknown", ended_at: null, exit_code: null });
  await page({ kind: "program", processId: "p1" }, context({ processes: { ...context().processes, items: [lost] } }), async host => {
    assert.match(host.textContent, /whether it is still running is not known/);
  });
});

test("a page whose item has gone says so and stays", async () => {
  await page({ kind: "program", processId: "gone" }, context(), async host => assert.match(host.textContent, /No longer here/));
  await page({ kind: "work", workId: "gone" }, context(), async host => assert.match(host.textContent, /No longer here/));
  await page({ kind: "routine", taskId: "gone" }, context(), async host => assert.match(host.textContent, /No longer here/));
  await page({ kind: "command", callId: "gone" }, context(), async host => assert.match(host.textContent, /No longer here/));
});

test("an item going away during a poll leaves the focus where the reader has it", async () => {
  const item = { id: "w1", text: "Send the invoice", note: null, status: "open", created_at: now - 100, updated_at: now - 50 };
  const held = extra => context({ work: { ...context().work, ...extra } });
  await page({ kind: "work", workId: "w1" }, held({ open_work: [item] }), async (host, click, render) => {
    const back = [...host.querySelectorAll("button")].find(button => button.textContent.includes("Activity"));
    assert.equal(document.activeElement, back);
    const composer = document.createElement("textarea"); document.body.append(composer);
    composer.focus();
    await render(held({ open_work: [] }));
    assert.match(host.textContent, /No longer here/);
    assert.equal(document.activeElement, composer);
    assert.equal([...host.querySelectorAll("button")].find(button => button.textContent.includes("Activity")), back);
    composer.remove();
  });
});

const call = (extra = {}) => ({ id: "c1", execution_id: "e1", session_id: "session", tool_call_id: "t1", name: "bash", description: "List the files", command: "ls  -la", started_at: now - 5, status: "running", ...extra });

test("a running command shows its state and command, and reads nothing yet", async () => {
  let reads = 0;
  globalThis.activityEvents = async () => { reads += 1; return { run_id: "e1", revision: "r", events: [] }; };
  await page({ kind: "command", callId: "c1" }, context({ processes: { ...context().processes, calls: [call()] } }), async host => {
    assert.match(host.textContent, /Command · Running · 5 s/);
    assert.match(host.textContent, /Command: ls -la/);
    assert.equal(reads, 0);
  });
});

test("a command that returned stays and shows what it printed", async () => {
  globalThis.activityEvents = async (session, runId) => {
    assert.equal(runId, "e1");
    return { run_id: "e1", revision: "r", events: [{ kind: "step", at: now, node_id: "n", tool_call_id: "t1", name: "bash", input: {}, input_truncated: false, result: "a.txt\nb.txt", result_truncated: false, failed: false }] };
  };
  const listed = context({ processes: { ...context().processes, calls: [call()] } });
  const returned = context({ processes: { ...context().processes, calls: [] } });
  await page({ kind: "command", callId: "c1" }, listed, async (host, click, render) => {
    await render(returned);
    await settle();
    assert.match(host.textContent, /Command · Returned/);
    assert.match(host.textContent, /a\.txt/);
  });
});

test("an interrupted command says the run stopped", async () => {
  await page({ kind: "command", callId: "c1" }, context({ processes: { ...context().processes, calls: [call({ status: "unknown" })] } }), async host => {
    assert.match(host.textContent, /Command · Interrupted/);
    assert.match(host.textContent, /interrupted before this call returned/);
  });
});

const work = (extra = {}) => ({ id: "w1", text: "Send Priya the draft", note: "She asked twice", status: "open", created_at: now - 900, updated_at: now - 60, done_at: null, ...extra });

test("open work is marked done, and Remove goes back to level 1", async () => {
  const ctx = context({ work: { ...context().work, open_work: [work()] } });
  await page({ kind: "work", workId: "w1" }, ctx, async (host, click) => {
    assert.match(host.textContent, /Send Priya the draft/);
    assert.match(host.textContent, /Note/);
    assert.match(host.textContent, /She asked twice/);
    await click("Mark done");
    assert.deepEqual(globalThis.workCalls[0], ["status", "session", "w1", "done"]);
    await click("Remove");
    assert.deepEqual(globalThis.workCalls[1], ["remove", "session", "w1"]);
    assert.equal(globalThis.closed, true);
  });
  await page({ kind: "work", workId: "w1" }, context({ work: { ...context().work, open_work: [work({ status: "done", done_at: now })] } }), async (host, click) => {
    await click("Reopen");
    assert.deepEqual(globalThis.workCalls.at(-1), ["status", "session", "w1", "open"]);
  });
});

const task = (extra = {}) => ({ id: "t1", title: "Watch fares", type: "recurring", enabled: true, created_at: now - 86400, cron: "0 * * * *", prompt: "Look up the fare", notes: "Cheapest seat only", runs: [], ...extra });

test("a scheduled task opens in the Scheduler and keeps its details in the Task row", async () => {
  await page({ kind: "routine", taskId: "t1" }, context({ work: { ...context().work, routines: [task()] } }), async (host, click) => {
    await settle();
    assert.match(host.textContent, /Watch fares/);
    assert.match(host.textContent, /Recurring · 0 \* \* \* \*/);
    await click("Open in Scheduler");
    assert.equal(globalThis.navigated, "/scheduler");
    await click("Task: Look up the fare");
    assert.match(host.textContent, /Cheapest seat only/);
    assert.match(host.textContent, /Task ID/);
    assert.match(host.textContent, /t1/);
  });
});

test("a webhook copies its address and key, and never shows the key", async () => {
  const hook = task({ type: "webhook", cron: undefined, prompt: "", hook_url: "http://h/hook", hook_key: "s3cret-key" });
  await page({ kind: "routine", taskId: "t1" }, context({ work: { ...context().work, routines: [hook] } }), async (host, click) => {
    await settle();
    await click("Task details");
    assert.match(host.textContent, /http:\/\/h\/hook/);
    assert.match(host.textContent, /X-OpenProgram-Key/);
    assert.doesNotMatch(host.textContent, /s3cret-key/);
    await click("Copy key");
    assert.deepEqual(globalThis.copied, ["key", "s3cret-key"]);
  });
});

test("a task's recent runs come from its record", async () => {
  const seen = [];
  globalThis.activityRuns = async (session, query) => { seen.push(query); return { runs: [], offset: 0, total: 0 }; };
  await page({ kind: "routine", taskId: "t1" }, context({ work: { ...context().work, routines: [task()] } }), async () => {
    await settle();
    assert.equal(seen[0].taskId, "t1");
  });
});

const alwaysOn = (extra = {}) => ({ enabled: true, agent_id: null, agent_name: "Scout", running: false, goal: { text: "x", status: "active" }, next_wake_at: now + 600, last_wake: null,
  config: { goal: "Watch the build", wake_minutes: 30, research: false, autonomy: "report" }, preferences: [{ id: "p1", text: "Skip passing builds", at: now }], reports: [], ...extra });
async function alwaysRow(ctx, scope, check) {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(createElement(ActivityPanelReact.Provider, { value: ctx }, createElement(ActivityPageFrame, { kind: null, title: "Agent", axis: null }, createElement(AlwaysOnRow, { context: ctx, scope })))));
  const click = async label => {
    const button = [...host.querySelectorAll("button")].find(item => item.textContent.includes(label));
    assert.ok(button, label);
    await act(async () => button.click());
  };
  try { await check(host, click); } finally { await act(async () => root.unmount()); host.remove(); }
}

test("the always-on row sits on the checked-out branch only, status first, details inside", async () => {
  const ctx = context({ work: { ...context().work, always_on: alwaysOn() } });
  await alwaysRow(ctx, { active: false }, async host => assert.doesNotMatch(host.textContent, /Always-on/));
  await alwaysRow(ctx, { active: true }, async (host, click) => {
    assert.match(host.textContent, /Always-on/);
    assert.match(host.textContent, /Next wake 18:00/);
    assert.doesNotMatch(host.textContent, /Standing goal/);
    await click("Always-on");
    assert.match(host.textContent, /Standing goal/);
    assert.match(host.textContent, /Watch the build/);
    assert.match(host.textContent, /Scout/);
    assert.match(host.textContent, /if a turn is running, tries again in 5 minutes/);
    assert.match(host.textContent, /keeps its history and reports/);
    assert.doesNotMatch(host.textContent, /at least daily|at once/);
    await click("Wake now");
    assert.deepEqual(globalThis.workCalls[0], ["wake", "session"]);
    await click("Skip passing builds");
    await click("Delete");
    assert.deepEqual(globalThis.workCalls[1], ["forget", "session", "p1"]);
  });
});

test("an interrupted check-in says so and offers no Retry", async () => {
  const ctx = context({ work: { ...context().work, always_on: alwaysOn({ last_wake: { at: now - 60, reason: "x", outcome: "interrupted", error: "Service restarted" } }) } });
  await alwaysRow(ctx, { active: true }, async (host, click) => {
    await click("Always-on");
    assert.match(host.textContent, /Service restarted/);
    assert.doesNotMatch(host.textContent, /Retry/);
  });
});

test("every report is reachable from the row, and one that never reached the chat says so", async () => {
  const stuck = { id: "abc", at: now - 60, title: "Build broke", body: "Red on main", links: [], in_chat: false };
  const posted = { id: "def", at: now - 90, title: "Build fine", body: "Green", links: [], in_chat: true };
  const ctx = context({ work: { ...context().work, always_on: alwaysOn({ reports: [stuck, posted] }) } });
  await alwaysRow(ctx, { active: true }, async (host, click) => {
    await click("Always-on");
    assert.match(host.textContent, /Build broke/);
    assert.match(host.textContent, /Not in the chat/);
    assert.match(host.textContent, /Build fine/);
    assert.equal(host.textContent.match(/Not in the chat/g).length, 1);
    await click("Build broke");
    assert.match(host.textContent, /Red on main/);
  });
});

test("an empty report goes by its record, else the conversation's only task", () => {
  const run = { id: "report_0123456789abcdefghij", is_report: true };
  const report = { id: "0123456789abcdefghijKLMN", at: now, title: "Build broke", body: "", links: [], kind: "finding" };
  const task = id => ({ id, title: `Task ${id}` });
  const named = (reports, routines) => reportNameForRun(run, context({ work: { ...context().work, always_on: alwaysOn({ reports, preferences: [] }), routines } }));
  assert.equal(named([report], [task("a")]), "Build broke");
  assert.equal(named([], [task("a")]), "Task a");
  assert.equal(named([], [task("a"), task("b")]), null);
  assert.equal(named([], []), null);
});

test("a report run finds its record by the message it was posted as", async () => {
  const report = { id: "0123456789abcdefghijKLMN", at: now, title: "Build broke", body: "Red on main", links: ["https://ci.example/1"], kind: "finding",
    deliveries: [{ channel: "telegram", account_id: "a", peer_id: "b", status: "failed", error: "signed out" }] };
  const ctx = context({ work: { ...context().work, always_on: alwaysOn({ reports: [report], preferences: [] }) } });
  const run = { id: "report_0123456789abcdefghij", is_report: true };
  assert.equal(reportForRun(run, ctx), true);
  assert.equal(reportForRun({ ...run, id: "report_other" }, ctx), false);
  assert.equal(reportForRun({ ...run, is_report: false }, ctx), false);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(createElement(ActivityPanelReact.Provider, { value: ctx }, createElement(ActivityPageFrame, { kind: null, title: "Agent", axis: null }, createElement(ReportInside, { context: ctx, run })))));
  assert.match(host.textContent, /Red on main/);
  assert.ok(host.querySelector('a[href="https://ci.example/1"]'));
  assert.ok(host.querySelector('[data-tone="err"]'));
  const button = [...host.querySelectorAll("button")].find(item => item.textContent.includes("Don't report things like this"));
  await act(async () => button.click());
  assert.deepEqual(globalThis.workCalls[0], ["prefer", "session", "Don't report: Build broke"]);
  await act(async () => root.unmount()); host.remove();
});

// The Agent page: a time chart.
const scope = (extra = {}) => ({ key: "h", head: "h", name: "main", active: true, color: null, updatedAt: null, members: null, ...extra });
const agentContext = (overrides = {}) => context({ scopes: [scope()], scopeOf: () => scope(), scopeHeadText: item => ({ kind: "agent", title: item.name, status: "", tone: "muted" }), ...overrides });
const chartRun = (id, extra = {}) => ({ id, message_id: id, user_message_id: `u_${id}`, execution_id: null, is_report: false, asked_by: "you", asked_preview: `Asked ${id}`, reply: `Reply ${id}`, started_at: now - 100,
  ended_at: now - 70, execution_status: null, message_status: "completed", reason_code: null, task_outcome: null, task_text: null, event_count: 0, revision: "r", steps: [], topic_id: "t1", topic_title: "Deployment", topic_order: 0, ...extra });
const oneRun = (...runs) => { globalThis.activityRuns = async (session, query) => { (globalThis.chartQueries ||= []).push(query); return { runs, offset: 0, total: runs.length }; }; };
const snapshotOf = (id, extra = {}) => ({ execution_id: id, session_id: "session", status: "running", capabilities: { pause: true, steer: true, cancel: true }, run_id: "r", revision_id: "v", status_version: 1, event_sequence: 0, ...extra });
const blockWith = (host, words) => [...host.querySelectorAll("[data-row-id]")].find(item => item.textContent.includes(words));

test("the Agent page opens on topics, with every level in its own column", async () => {
  oneRun(chartRun("a", { steps: [{ kind: "step", at: now - 90, node_id: "n", name: "bash", input: { command: "ls" }, failed: false, result: null }] }));
  await page({ kind: "agent", scopeKey: "h" }, agentContext(), async host => {
    await settle();
    assert.equal(globalThis.chartQueries[0].chart, true);
    assert.match(host.textContent, /TopicsMessagesStepsInside a step|Topics.*Deployment.*Messages.*Asked a.*Steps/s);
    assert.equal(host.querySelectorAll("[title]").length, 0);
    // A block with a next column leads there; one call opens its details in place.
    const blocks = [...host.querySelectorAll("[data-chart-id]")];
    const call = blocks.find(item => item.hasAttribute("aria-expanded"));
    assert.equal(blocks.filter(item => item.hasAttribute("aria-expanded")).length, 1);
    assert.equal(blocks.filter(item => item.getAttribute("role") === "button").length, blocks.length, "every block can be clicked");
    assert.equal(call.getAttribute("aria-expanded"), "false");
    assert.doesNotMatch(host.textContent, /Input/);
    call.click();
    await settle();
    assert.equal(call.getAttribute("aria-expanded"), "true");
    assert.match(call.textContent, /Inputls$/, "the command itself, and no Result row for a call that returned nothing");
    call.click();
    await settle();
    assert.equal(call.getAttribute("aria-expanded"), "false");
    assert.doesNotMatch(host.textContent, /Input/);
  });
});

test("a finished or failed message has no buttons, a running one does and never Retry", async () => {
  oneRun(chartRun("a", { execution_id: "e1", execution_status: "failed" }), chartRun("b", { execution_id: "e2", execution_status: "running", ended_at: null, topic_id: "t2", topic_title: "Figures", topic_order: 1 }));
  const controller = { executions: [snapshotOf("e1", { status: "failed", capabilities: { retry: true } }), snapshotOf("e2")], command: async command => { (globalThis.commands ||= []).push(command); return { status: "accepted", command_id: command.command_id }; } };
  globalThis.commands = [];
  await page({ kind: "agent", scopeKey: "h" }, agentContext({ controller }), async host => {
    await settle();
    const buttons = [...host.querySelectorAll("button")].map(item => item.textContent);
    assert.equal(buttons.filter(label => label === "Pause").length, 1);
    assert.equal(buttons.filter(label => label === "Cancel").length, 1);
    assert.equal(buttons.filter(label => label === "Add instruction").length, 1);
    assert.doesNotMatch(host.textContent, /Retry/);
  });
});

test("clicking a block moves right and opens nothing downward", async () => {
  oneRun(chartRun("a", { steps: [{ kind: "step", at: now - 90, node_id: "n", name: "bash", input: { command: "ls" }, failed: false, result: null }] }));
  await page({ kind: "agent", scopeKey: "h" }, agentContext(), async host => {
    await settle();
    const before = host.textContent;
    const topicBlock = blockWith(host, "Deployment");
    assert.ok(topicBlock);
    await act(async () => topicBlock.click());
    await settle();
    assert.equal(host.textContent, before);
  });
});

test("each branch is its own chart, and only one not checked out offers to switch", async () => {
  oneRun();
  const switched = [];
  const alt = scope({ key: "alt", head: "h_alt", name: "Try another wording", active: false });
  await page({ kind: "agent", scopeKey: "alt" }, agentContext({ scopes: [alt], scopeOf: () => alt, switchTo: item => switched.push(item.head) }), async (host, click) => {
    await settle();
    assert.equal(globalThis.chartQueries.at(-1).head, "h_alt");
    await click("Switch to this branch");
    assert.deepEqual(switched, ["h_alt"]);
  });
  globalThis.chartQueries = [];
  await page({ kind: "agent", scopeKey: "h" }, agentContext(), async host => { await settle(); assert.doesNotMatch(host.textContent, /Switch to this branch/); });
});

test("the next wake opens the always-on setting, and back returns to the chart", async () => {
  oneRun(chartRun("a"));
  const ctx = agentContext({ work: { ...context().work, always_on: alwaysOn({ next_wake_at: now + 600, config: { wake_minutes: 60 } }) } });
  await page({ kind: "agent", scopeKey: "h" }, ctx, async (host, click) => {
    await settle();
    assert.match(host.textContent, /Next wake.*Always-on · hourly/s);
    await act(async () => blockWith(host, "Next wake").click());
    assert.match(host.textContent, /Wake now|Always-on/);
    await click("Activity");
    assert.equal(globalThis.closed, undefined);
    assert.ok(blockWith(host, "Deployment"));
  });
});

test("while topics are being sorted the chart reads again until they land", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reads = 0;
  globalThis.activityRuns = async () => { reads += 1; return { runs: [chartRun("a", reads > 1 ? {} : { topic_id: null, topic_title: null, topic_order: null })], offset: 0, total: 1, topics_sorting: reads === 1 }; };
  await page({ kind: "agent", scopeKey: "h" }, agentContext(), async host => {
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { t.mock.timers.tick(4100); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    assert.ok(reads >= 2);
  });
});
