import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/components/agents/agent-avatar") return { url: "data:text/javascript,export const AgentAvatarById = () => null; export const useConversationAgentShape = () => 'cloud'", shortCircuit: true };
    if (specifier === "@/lib/execution/use-execution-debugger") return { url: "data:text/javascript,export const useExecutionDebugger = () => globalThis.activityController", shortCircuit: true };
    if (specifier === "@/lib/execution/use-managed-processes") return { url: "data:text/javascript,export const useManagedProcesses = () => globalThis.activityProcesses", shortCircuit: true };
    if (specifier === "../../debugger/debugger-panel") return { url: "data:text/javascript,export const DebuggerPanel = () => 'DEBUGGER'", shortCircuit: true };
    if (specifier === "@/lib/net/execution/activity-runs-client") return { url: "data:text/javascript,export const getActivityRuns = (...a) => globalThis.activityRuns(...a); export const getActivityRunEvents = (...a) => globalThis.activityEvents(...a)", shortCircuit: true };
    if (specifier === "@/lib/net/execution/activity-roster-client") return { url: "data:text/javascript,export const getActivityRoster = async () => globalThis.activityRoster ?? { agents: {}, outgoing: [] }", shortCircuit: true };
    if (specifier === "@/lib/nav/navigate") return { url: "data:text/javascript,export const navigate = (path) => { (globalThis.navigated ||= []).push(path); }", shortCircuit: true };
    if (specifier === "./agent-page") return { url: new URL("./fixtures/list-agent-page.tsx", import.meta.url).href, shortCircuit: true };
    if (specifier === "./program-page") return { url: "data:text/javascript,export const ProgramPage = () => 'PROGRAM PAGE'", shortCircuit: true };
    if (specifier === "./command-page") return { url: "data:text/javascript,export const CommandPage = () => 'COMMAND PAGE'", shortCircuit: true };
    if (specifier === "./scheduled-task-page") return { url: "data:text/javascript,export const ScheduledTaskPage = () => 'TASK PAGE'", shortCircuit: true };
    if (specifier === "./task-runs-page") return { url: "data:text/javascript,export const TaskRunsPage = () => 'TASK RUNS PAGE'", shortCircuit: true };
    if (specifier === "./open-work-page") return { url: "data:text/javascript,export const OpenWorkPage = () => 'WORK PAGE'", shortCircuit: true };
    if (specifier === "./always-on-row") return { url: "data:text/javascript,export const AlwaysOnRow = () => null; export const ReportInside = () => null; export const reportForRun = () => false; export const reportNameForRun = () => null", shortCircuit: true };
    if (specifier === "@/lib/agents/pending-inbox") return { url: "data:text/javascript,export const usePendingInbox = () => ({ items: [], loaded: true }); export const pendingInOrder = (items) => items; export const openPendingItem = () => {}", shortCircuit: true };
    if (specifier === "@/components/chat/messages/cards/sub-agent-transcript") return { url: "data:text/javascript,export const SubAgentTranscript = () => null", shortCircuit: true };
    if (specifier === "@/lib/chat/transcript/sub-agent-transcripts") return { url: "data:text/javascript,export const loadSubAgentTranscript = async () => globalThis.agentTip ?? null", shortCircuit: true };
    if (specifier === "@/lib/runtime-bridge/session/session-load") return { url: "data:text/javascript,export const sendRuntimeCommand = (p) => { (globalThis.sentCommands ||= []).push(p); }", shortCircuit: true };
    if (specifier === "@/lib/runtime-bridge/state") return { url: "data:text/javascript,export const getSocket = () => globalThis.approvalSocket; export const runtimeState = globalThis.runtimeStateStub ||= { _branchesByConv: {}, _branchLaneColorMap: {} }", shortCircuit: true };
    if (specifier.endsWith(".module.css")) {
      return { url: "data:text/javascript,export default {}", shortCircuit: true };
    }
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        if (existsSync(fileURLToPath(base + suffix))) {
          return { url: base + suffix, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".tsx")) {
      return {
        format: "module", shortCircuit: true,
        source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
          compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
        }).outputText,
      };
    }
    return nextLoad(url, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
globalThis.HTMLElement = window.HTMLElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.CSS = { escape: value => value };
// linkedom has no focus model and no scrolling; the keyboard test needs both.
window.HTMLElement.prototype.focus = function () { document.activeElement = this; this.dispatchEvent(new Event("focusin", { bubbles: true })); };
window.HTMLElement.prototype.scrollIntoView = function () {};
globalThis.fetch = async () => ({ ok: false, status: 404, async json() { return {}; }, async text() { return "{}"; } });
// Text assertions select a browser preference, independent of the host OS.
globalThis.localStorage = { getItem(key) { return key === "agentic_locale" ? "en" : null; }, setItem() {}, removeItem() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(0), 0);
globalThis.cancelAnimationFrame = clearTimeout;
window.location = { pathname: "/chat", hash: "", search: "" };
window.history = { replaceState() {}, pushState() {} };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { RunningPanel } = await import("../../../components/right-sidebar/running/running-panel.tsx");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { forgetActivityReads } = await import("../../../lib/execution/activity-reads.ts");
useSessionStore.setState({ conversations: { session: { id: "session", title: "NeurIPS trip" } } });
const DAY = 86400;
const now = Math.floor(Date.now() / 1000);
const run = (id, status, started_at, extra = {}) => ({ execution_id: id, job_id: id, session_id: "session", status, started_at, updated_at: started_at, parent_execution_id: null, effect_summary: {}, task_label: id, capabilities: { pause: false, step: false, steer: false, retry: false, fork: false }, status_version: 1, active_child_ids: [], ...extra });
async function mounted(executions, check, processes = [], stateOverrides = {}, processOverrides = {}) {
  const selected = [];
  globalThis.activityController = { executions, branches: [], connection: { state: "connected" }, fetchedAt: 1, selectExecution(id) { selected.push(id); },
    command: async (command) => { (globalThis.commands ||= []).push(command); return { status: "accepted", command_id: command.command_id }; }, ...stateOverrides };
  globalThis.activityProcesses = { items: processes, calls: [], stale: false, loaded: true, refresh() {}, ...processOverrides };
  globalThis.commands = []; globalThis.sentCommands = [];
  globalThis.activityEventReads = [];
  globalThis.activityRuns ??= async () => ({ runs: [], offset: 0, total: 0 });
  globalThis.activityEvents = async (session, runId) => { globalThis.activityEventReads.push(runId); return { run_id: runId, revision: "r", events: globalThis.runEvents?.[runId] ?? [] }; };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    const render = props => act(async () => root.render(createElement(RunningPanel, { active: true, sessionId: "session", ...props })));
    await render();
    const click = async text => {
      const button = [...host.querySelectorAll("button, [role=button]")].find(b => b.textContent.includes(text));
      assert.ok(button, text);
      await act(async () => button.click());
    };
    await check(host, click, selected, render);
    // What one test's pages read is not the next test's.
  } finally { await act(async () => root.unmount()); host.remove(); forgetActivityReads(); }
}

const agentCall = { id: "c1", execution_id: "turn", name: "agent", description: "Fix page close",
  prompt: "Inspect the close branch and fix it", command: "", started_at: now - 3 * DAY, status: "unknown" };
const spawned = run("j_fix", "reconciliation_required", now - 3 * DAY + 2, { parent_execution_id: "turn",
  display: { kind: "job_agent", label: "Fix_page_close", assistant_message_id: "j_fix_user_reply" }, effect_summary: { provider_response_incomplete: true } });

const activityRun = (id, started_at, extra = {}) => ({ id, message_id: id, user_message_id: `${id}_user`, execution_id: null, is_report: false, asked_by: "you", asked_preview: "the question",
  reply: `Reply ${id}`, reply_truncated: false, started_at, ended_at: started_at + 36, execution_status: null, message_status: "completed", reason_code: null, task_outcome: null, task_text: null, event_count: 1, revision: "r", ...extra });
const listRuns = runs => { globalThis.activityRuns = async (session, query) => ({ runs: runs.slice(query.offset ?? 0, (query.offset ?? 0) + (query.limit ?? runs.length)), offset: query.offset ?? 0, total: runs.length }); };
const asked = (at, text = "the question") => ({ kind: "asked", at, by: "you", text, truncated: false });
const replied = (at, text = "the answer") => ({ kind: "replied", at, text, truncated: false });
const step = (at, command, extra = {}) => ({ kind: "step", at, node_id: `node_${command}`, tool_call_id: null, name: "bash", input: { command }, input_truncated: false, result: "output", result_truncated: false, failed: false, ...extra });
const finishedTurn = [run("turn", "completed", now - 300, { terminal_at: now - 200 })];
const expanded = host => [...host.querySelectorAll("[data-row-id][aria-expanded=true]")].map(row => row.getAttribute("data-row-id"));
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
const key = async (target, name, extra = {}) => act(async () => {
  const event = new Event("keydown", { bubbles: true, cancelable: true });
  Object.assign(event, { key: name, ...extra });
  target.dispatchEvent(event);
});
const row = (host, text) => [...host.querySelectorAll("[data-row-id]")].find(item => item.textContent.includes(text));

test.afterEach(() => { globalThis.activityRuns = undefined; globalThis.runEvents = undefined; });

const usage = (cost_usd, tokens = 1000, tool_calls = 1) => ({ tokens, cost_usd, tool_calls });
const helper = (id, status, label, extra = {}) => run(id, status, now - 100, { parent_execution_id: "live", display: { kind: "job_agent", label }, ...extra });
const rosterButton = (host, text) => [...host.querySelectorAll("[data-roster-row]")].find(item => item.textContent.includes(text));

// A sub-agent as the chart's runs tell it: a step of the message that started it.
const started = (job_id, name, at, ended_at, extra = {}) => ({ kind: "subagent", at, ended_at, job_id, name, agent_id: null, context: null, status: ended_at === null ? "running" : "completed", never_started: false, failed: false,
  brief: `Brief of ${name}`, brief_truncated: false, result: null, result_truncated: false, error: null, created_at: at, closed_as: null, conversation: null, children: [], steps: [], ...extra });
const glyph = (host, name) => host.querySelector(`button[aria-label^="${name}"]`);
/** The main agent's page: from its glyph on the chart, or from its row before the conversation has a run. */
const openMain = async (host, click) => {
  await settle();
  const mark = glyph(host, "Main agent");
  if (mark) await act(async () => mark.click()); else await click("Main agent");
};

test("the agents are on the chart: a glyph says what its agent cost, and the main agent's block stops the sub-agents still going", async () => {
  globalThis.activityRoster = { agents: {
    j_live: { agent_status: "running", queued: false, usage: usage("0.020000", 18400, 12) },
    j_done: { agent_status: "idle", queued: false, usage: usage("0.030000") },
  }, outgoing: [] };
  const posted = [];
  const fetchBefore = globalThis.fetch;
  globalThis.fetch = async (url, init) => { if (String(url).startsWith("/api/execution/")) posted.push([String(url), JSON.parse(init?.body ?? "{}")]); return { ok: true, status: 200, async json() { return { status: "accepted" }; } }; };
  listRuns([activityRun("live", now - 120, { ended_at: now - 80, steps: [started("j_done", "Research", now - 110, now - 90), started("j_live", "Form filler", now - 100, null)] })]);
  try {
    await mounted([
      run("live", "completed", now - 120, { terminal_at: now - 80 }),
      run("j_live", "running", now - 100, { parent_execution_id: "live", display: { kind: "job_agent", label: "Form_filler" } }),
      run("j_done", "completed", now - 110, { parent_execution_id: "live", display: { kind: "job_agent", label: "Research" } }),
    ], async host => {
      await settle();
      // Nothing lists the agents above the chart: the panel starts with it.
      assert.match(host.textContent, /^Topics/);
      // Each agent has one glyph; pointed at, it says the agent and what it cost.
      assert.equal(host.querySelectorAll("button[aria-label]").length - host.querySelectorAll("[data-chart-back]").length, 3);
      assert.equal(glyph(host, "Main agent").getAttribute("aria-label"), "Main agent · sub-agents $0.05");
      assert.equal(glyph(host, "Form filler").getAttribute("aria-label"), "Form filler · Running · $0.02 · 18.4k tokens · 12 tool calls");
      // A sub-agent is a step of the message that started it.
      assert.match(host.querySelector('[data-chart-column="2"]').textContent, /Form filler· \d+m \d+s.*Research· 20s/);
      // The one still going is told by its glyph.
      assert.equal(host.querySelectorAll('[data-chart-column="2"] [data-live]').length, 1);
      // Stopping reaches the sub-agent still going, and only that one.
      await act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === "Stop sub-agents").click());
      await settle();
      assert.deepEqual(posted.map(([url, body]) => [url, body.action, body.execution_id]), [["/api/execution/cancel", "execution.cancel", "j_live"]]);
    });
  } finally { globalThis.fetch = fetchBefore; globalThis.activityRoster = undefined; }
});

test("a glyph counts what waits on the reader and opens its agent, and a message still unanswered is a row of its own", async () => {
  globalThis.activityRoster = { agents: { j_run: { agent_status: "running", queued: false, usage: usage("0.020000") } },
    outgoing: [{ kind: "outgoing", job_id: "d1", sender: "session", to_session: "butler", to: "Butler", status: "waiting", sent_at: now - 30 }] };
  useSessionStore.setState({ pendingDecisions: [{ id: "decision", sessionId: "session", executionId: "j_ask", waitGeneration: 1, expectedVersion: 1, kind: "approval", prompt: "[PR Verifier] Run the tests?", options: ["Allow", "Deny"], multi: false, allow_custom: false }] });
  globalThis.agentTip = "j_run_reply"; globalThis.navigated = [];
  listRuns([activityRun("live", now - 120, { ended_at: now - 80, steps: [started("j_run", "Explorer", now - 110, null), started("j_ask", "PR Verifier", now - 100, null)] })]);
  try {
    await mounted([run("live", "completed", now - 120, { terminal_at: now - 80 }), helper("j_run", "running", "Explorer"), helper("j_ask", "paused", "PR_Verifier")], async host => {
      await settle();
      // The request for approval is the red count on the sub-agent that asks and on the main agent that started it.
      assert.deepEqual(["Main agent", "PR Verifier", "Explorer"].map(name => glyph(host, name).textContent), ["1", "1", ""]);
      // A sub-agent's glyph goes where its card's Switch goes.
      await act(async () => glyph(host, "Explorer").click());
      await settle();
      assert.deepEqual(globalThis.sentCommands.map(command => [command.action, command.head_msg_id]), [["checkout_branch", "j_run_reply"], ["load_session", undefined]]);
      // A message to another conversation says so to a screen reader; the arrow is decoration.
      assert.match(host.textContent, /^Sent messages→ ButlerWaiting/);
      assert.equal(rosterButton(host, "Butler").getAttribute("aria-label"), "Message sent to Butler, Waiting");
      await act(async () => rosterButton(host, "Butler").click());
      assert.deepEqual(globalThis.navigated, ["/s/butler"]);
      // The main agent's glyph opens its page.
      await act(async () => glyph(host, "Main agent").click());
      assert.ok(host.querySelector("[data-timeline]"));
    });
  } finally { useSessionStore.setState({ pendingDecisions: [] }); globalThis.activityRoster = undefined; globalThis.agentTip = undefined; }
});

test("a sub-agent the server says is waiting for approval has the red count and the words, also while the page holds no question of it", async () => {
  globalThis.activityRoster = { agents: {
    j_wait: { agent_status: "needs approval", queued: false, usage: usage("0.020000") },
    j_slow: { agent_status: "stalled", queued: false, usage: null },
  }, outgoing: [] };
  listRuns([activityRun("live", now - 120, { ended_at: now - 80, steps: [started("j_wait", "Explorer", now - 110, null), started("j_slow", "Scout", now - 100, null)] })]);
  try {
    await mounted([run("live", "completed", now - 120, { terminal_at: now - 80 }), helper("j_wait", "running", "Explorer"), helper("j_slow", "running", "Scout")], async host => {
      await settle();
      assert.equal(glyph(host, "Explorer").getAttribute("aria-label"), "Explorer · Needs approval · $0.02 · 1.0k tokens · 1 tool call");
      assert.equal(glyph(host, "Main agent").getAttribute("aria-label"), "Main agent · 1 waiting for you · sub-agents $0.02");
      // No progress is said, and is not a thing to answer.
      assert.equal(glyph(host, "Scout").getAttribute("aria-label"), "Scout · Stalled");
      assert.deepEqual(["Main agent", "Explorer", "Scout"].map(name => glyph(host, name).textContent), ["1", "1", ""]);
    });
  } finally { globalThis.activityRoster = undefined; }
});

test("a helper's tip says what it used, with whatever is known", async () => {
  const { usageTip } = await import("../../../components/right-sidebar/running/agent-roster.tsx");
  const english = value => value;
  assert.equal(usageTip({ agent_status: "running", queued: false, usage: usage("0.02", 18400, 12) }, english), "18.4k tokens · 12 tool calls");
  assert.equal(usageTip({ agent_status: "running", queued: false, usage: usage(null, null, 1) }, english), "1 tool call");
  assert.equal(usageTip(null, english), "");
});

test("before its first run the main agent is one row where the chart will be", async () => {
  await mounted([run("live", "running", now - 120)], async host => {
    await settle();
    assert.equal(host.textContent, "Main agentRunning · 2 min");
    assert.equal(host.querySelector("[data-chart-column]"), null);
  });
});

test("a scheduled task that runs a prompt is one row under Scheduled tasks, and its runs and their helpers are none", async () => {
  const { rememberActivityRead } = await import("../../../lib/execution/activity-reads.ts");
  const summary = { runs: 178, cost: 20.89, unfinished: 9, notified: 1, last_at: now - 600, recent: ["quiet", "failed", "stopped", "notified"] };
  rememberActivityRead("work", "session", { session_id: "session", always_on: { enabled: false, reports: [] }, open_work: [], routines: [
    { id: "t1", title: "Check stock hourly", type: "recurring", enabled: true, created_at: now - 9 * DAY, cron: "0 * * * *", prompt: "check", summary },
    { id: "t2", title: "Call mom", type: "once", enabled: true, created_at: now - DAY, run_at: "2026-11-20T09:00:00", reminder: "Call mom" },
  ] }, { at: Date.now(), moves: 0 });
  globalThis.activityRoster = { agents: { j_dup: { agent_status: "failed", queued: false, usage: usage("0.01") }, j_wait: { agent_status: "running", queued: false, usage: usage("0.20") } }, outgoing: [] };
  try {
    await mounted([
      run("turn", "completed", now - 300, { terminal_at: now - 200 }),
      run("538f9a29", "failed", now - 3600, { scheduled_task_id: "t1", task_label: "538f9a29" }),
      run("j_dup", "failed", now - 3500, { parent_execution_id: "538f9a29", display: { kind: "job_agent", label: "Stock_checker" } }),
      run("report_7", "completed", now - 1800, { scheduled_task_id: "t1", task_label: "report_7" }),
      run("j_wait", "paused", now - 1700, { parent_execution_id: "report_7", display: { kind: "job_agent", label: "Browser" } }),
    ], async (host, click) => {
      await settle();
      // A scheduled run's helper that waits on the reader is the red count on the main agent.
      assert.match(host.textContent, /^1Main agent/);
      // The task is one row with its runs in short, beside the reminders, and never twice.
      assert.match(host.textContent, /Scheduled tasksCheck stock hourly178 runs · 9 didn't finish · last.*Call mom/);
      assert.equal(host.textContent.split("Check stock hourly").length, 2);
      // No bare-id rows, and the scheduled run's helpers are the task's.
      assert.doesNotMatch(host.textContent, /538f9a29|report_7|Stock checker|Browser/);
      assert.deepEqual([...[...host.querySelectorAll("button")].find(item => item.textContent.startsWith("Check stock hourly")).querySelectorAll("i[data-tone]")].map(cell => cell.getAttribute("data-tone")), ["ok", "err", "warn", "ok"]);
      await click("Check stock hourly");
      assert.match(host.textContent, /TASK RUNS PAGE/);
    });
  } finally { globalThis.activityRoster = undefined; rememberActivityRead("work", "session", null, { at: 0, moves: 0 }); }
});

test("an always-on conversation's next wake is the chart's first row, and opens the main agent's page", async () => {
  const { rememberActivityRead } = await import("../../../lib/execution/activity-reads.ts");
  rememberActivityRead("work", "session", { session_id: "session", always_on: { enabled: true, next_wake_at: now + 600, config: { wake_minutes: 60 }, reports: [] }, open_work: [], routines: [] }, { at: Date.now(), moves: 0 });
  listRuns([activityRun("a", now - 300)]);
  try {
    await mounted([], async (host, click) => {
      await settle();
      assert.match(host.querySelector('[data-chart-column="0"]').textContent, /Next wakeAlways-on · hourly/);
      await click("Next wake");
      assert.ok(host.querySelector("[data-timeline]"));
    });
  } finally { rememberActivityRead("work", "session", null, { at: 0, moves: 0 }); }
});

test("a conversation whose turns left no execution record still lists its main agent, and empty groups are not shown", async () => {
  useSessionStore.setState({ conversations: { session: { id: "session", title: "NeurIPS trip", updated_at: now - 60 } } });
  try {
    await mounted([], async (host, click) => {
      await settle();
      assert.match(host.textContent, /^Main agent\d{1,2}:\d{2}/);
      assert.doesNotMatch(host.textContent, /Programs and commands|Scheduled tasks/);
      await openMain(host, click);
      assert.match(host.textContent, /NeurIPS trip/);
    });
  } finally {
    useSessionStore.setState({ conversations: { session: { id: "session", title: "NeurIPS trip" } } });
  }
});

test("a running program has a level-1 row that opens its page; a command the agent waits on reads on the agent", async () => {
  await mounted([], async (host, click) => {
    await settle();
    assert.match(host.textContent, /^Main agentRunning bash · \d+ s/);
    assert.match(host.textContent, /Programs and commandspythonpython worker\.pyRunning · \d+ s$/);
    assert.doesNotMatch(host.textContent, /Command · Running/);
    await click("python worker.py");
    assert.match(host.textContent, /PROGRAM PAGE/);
  }, [{ id: "proc", command: "python worker.py", status: "running", started_at: now, backend_id: "local" }], {},
  { calls: [{ id: "call", name: "bash", command: "sleep 30", started_at: now - 5, status: "running" }] });
});

test("a program or command that ended leaves Programs and commands, failed or not", async () => {
  const program = (id, status, extra = {}) => ({ id, command: `python ${id}.py`, status, started_at: now - 60, ended_at: now - 57, backend_id: "local", ...extra });
  const ended = [program("done", "exited", { exit_code: 0 }), program("finished", "completed"), program("halted", "stopped", { exit_code: -15 }),
    program("cut", "interrupted"), program("lost", "unknown", { ended_at: null })];
  const cutCall = { id: "cut-call", execution_id: "gone", name: "bash", description: "Build the site", command: "make", started_at: now - 3 * DAY, status: "unknown" };
  // Nothing going: the group, header included, is gone, even with failures.
  await mounted([], async host => {
    assert.doesNotMatch(host.textContent, /Programs and commands|python|Build the site/);
  }, [...ended, program("crashed", "failed"), program("broke", "exited", { exit_code: 1 })], {}, { calls: [cutCall] });
  // A program that lost its supervisor does not keep its agent reading Running.
  await mounted(finishedTurn, async host => {
    assert.doesNotMatch(host.textContent, /Running|Programs and commands|lost\.py/);
  }, [program("lost", "unknown", { ended_at: null, execution_id: "turn" })]);
  await mounted([], async (host, click) => {
    assert.match(host.textContent, /Programs and commandspythonpython live\.pyRunning · 5 min/);
    assert.doesNotMatch(host.textContent, /done\.py|finished\.py|halted\.py|cut\.py|lost\.py|broke\.py|crashed\.py|Completed|finished|Exited|Failed/);
    await click("python live.py");
    assert.match(host.textContent, /PROGRAM PAGE/);
  }, [...ended, program("crashed", "failed", { started_at: now - 90 }), program("broke", "exited", { exit_code: 1 }), program("live", "running", { started_at: now - 300, ended_at: null })]);
});

test("calls do not flash as unowned before the executions are read", async () => {
  await mounted([], async host => {
    assert.doesNotMatch(host.textContent, /Needs you|Fix page close/);
  }, [], { fetchedAt: null }, { calls: [agentCall] });
});

test("Activity never flashes an error for loading, reconnection, or another conversation's failure", async () => {
  for (const connection of [{state:"reconnecting"}, {state:"gap"}, {state:"stale"}, {state:"stale",message:"previous failure",errorSessionId:"other"}]) {
    await mounted([], async host => {
      assert.doesNotMatch(host.textContent, /statuses are unavailable|Could not load activity/);
    }, [], {connection,fetchedAt:null}, {loaded:true});
  }
});

test("a failed refresh keeps the rows it read and says nothing", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const processFailed of [false, true]) await mounted(finishedTurn, async host => {
    await act(async () => { t.mock.timers.tick(30000); });
    assert.match(host.textContent, /Main agent/);
    assert.doesNotMatch(host.textContent, /Updating status|saved records|Could not read|unavailable|Retrying/);
  }, [], {connection: processFailed ? {state:"connected"} : {state:"stale",message:"request failed",errorSessionId:"session"}}, {stale:processFailed});
});

test("before the executions are read the main agent is listed, resting", async () => {
  await mounted([], async host => {
    await settle();
    assert.match(host.textContent, /Main agent/);
    assert.doesNotMatch(host.textContent, /Loading|Updating|Could not read/);
  }, [], { fetchedAt: null, connection: { state: "reconnecting" } }, { loaded: false });
});

test("runs that could never be read say nothing and offer no Retry: the reads go on behind the main agent", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  await mounted([], async host => {
    await act(async () => { t.mock.timers.tick(60000); });
    assert.match(host.textContent, /Main agent/);
    assert.doesNotMatch(host.textContent, /Could not read|Retry|Loading|unavailable/);
    assert.equal([...host.querySelectorAll("[role=alert]")].length, 0);
  }, [], { fetchedAt: null, connection: { state: "stale", message: "request failed", errorSessionId: "session" }, refresh: async () => false });
});

test("the agent page is the head, then the runs under day labels on one axis", async () => {
  listRuns([activityRun("newest", now - 300, { ended_at: now - 196 }), activityRun("older", now - 2 * DAY)]);
  await mounted([run("turn", "completed", now - 300, { terminal_at: now - 196 })], async (host, click) => {
    await openMain(host, click);
    const text = host.textContent;
    assert.match(text, /Activity/);
    assert.match(text, /Main agent|NeurIPS trip/);
    assert.doesNotMatch(text, /Each bar is one run/);
    assert.match(text, /Reply newest.*\d\d:\d\d · 1m 44s/s);
    assert.match(text, /0123 min/);
    assert.equal(host.querySelectorAll("[title]").length, 0);
  });
});

test("only an opened row reads its events, and rows stay open however many are opened", async () => {
  listRuns([activityRun("a", now - 300), activityRun("b", now - 200 - DAY), activityRun("c", now - 100 - DAY)]);
  globalThis.runEvents = { a: [asked(now - 300), replied(now - 280)], b: [asked(now - 200 - DAY)], c: [] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    assert.deepEqual(globalThis.activityEventReads, []);
    assert.deepEqual(expanded(host), []);
    await click("Reply a");
    assert.deepEqual(globalThis.activityEventReads, ["a"]);
    assert.match(host.textContent, /Asked: the question.*Replied: the answer/s);
    await click("Reply b");
    assert.deepEqual(expanded(host), ["a", "b"]);
    await click("Reply a");
    assert.deepEqual(expanded(host), ["b"]);
    assert.doesNotMatch(host.textContent, /Replied: the answer/);
  });
});

test("the newest run opens by itself only when it failed, waits or runs, and a close sticks", async () => {
  listRuns([activityRun("fine", now - 300)]);
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    assert.deepEqual(expanded(host), []);
  });
  listRuns([activityRun("bad", now - 300, { reply: "[error] boom" }), activityRun("older", now - 900)]);
  globalThis.runEvents = { bad: [asked(now - 300)] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    assert.deepEqual(expanded(host), ["bad"]);
    await click("boom");
    assert.deepEqual(expanded(host), []);
    await act(async () => { window.dispatchEvent(new Event("op:execution-update")); });
    await settle();
    assert.deepEqual(expanded(host), []);
  });
});

test("a poll replaces rows without closing them, and keeps what was shown", async () => {
  const runs = Array.from({ length: 25 }, (_, index) => activityRun(`r${index}`, now - 300 - index * 60));
  listRuns(runs);
  globalThis.runEvents = { r1: [asked(now - 360)] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    assert.match(host.textContent, /Show 5 earlier/);
    await click("Show 5 earlier");
    await settle();
    assert.match(host.textContent, /Reply r24/);
    await click("Reply r1");
    const before = row(host, "Reply r1");
    listRuns([activityRun("fresh", now - 10), ...runs.map(item => item.id === "r2" ? { ...item, reply: "Changed" } : item)]);
    await act(async () => { window.dispatchEvent(new Event("op:execution-update")); });
    await settle();
    assert.match(host.textContent, /Reply fresh/);
    assert.match(host.textContent, /Changed/);
    assert.equal(row(host, "Reply r1"), before);
    assert.deepEqual(expanded(host), ["r1"]);
  });
});

test("a new run does not take the oldest row, or its open content, off the page", async () => {
  const runs = Array.from({ length: 20 }, (_, index) => activityRun(`r${index}`, now - 300 - index * 60));
  listRuns(runs);
  globalThis.runEvents = { r19: [asked(now - 1440, "the oldest question")] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply r19");
    assert.deepEqual(expanded(host), ["r19"]);
    listRuns([activityRun("fresh", now - 10), ...runs]);
    await act(async () => { window.dispatchEvent(new Event("op:execution-update")); });
    await settle();
    assert.match(host.textContent, /Reply fresh/);
    assert.deepEqual(expanded(host), ["r19"]);
    assert.match(host.textContent, /the oldest question/);
    assert.doesNotMatch(host.textContent, /earlier/);
  });
});

test("past the server's page size a new run removes nothing, and Show more continues in order", async () => {
  const runs = Array.from({ length: 300 }, (_, index) => activityRun(`r${index}`, now - 300 - index * 60));
  listRuns(runs);
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Show 50 earlier");
    await settle();
    await click("Show 50 earlier");
    await settle();
    listRuns([activityRun("fresh", now - 10), ...runs]);
    await act(async () => { window.dispatchEvent(new Event("op:execution-update")); });
    await settle();
    const shown = [...host.querySelectorAll("[data-row-id]")].map(item => item.getAttribute("data-row-id")).filter(id => /^(r\d+|fresh)$/.test(id));
    assert.deepEqual(shown, ["fresh", ...runs.slice(0, 120).map(item => item.id)]);
    await click("Show 50 earlier");
    await settle();
    const more = [...host.querySelectorAll("[data-row-id]")].map(item => item.getAttribute("data-row-id")).filter(id => /^r\d+$/.test(id));
    assert.deepEqual(more, runs.slice(0, 170).map(item => item.id));
  });
});

test("an open finished run reads again until its background sub-agent has ended", async () => {
  listRuns([activityRun("a", now - 300)]);
  const agent = extra => ({ kind: "subagent", at: now - 290, ended_at: null, job_id: "j_bg", name: "Background", agent_id: null, context: null, status: "running", never_started: false, failed: false,
    brief: "go", brief_truncated: false, result: null, result_truncated: false, error: null, created_at: now - 290, closed_as: null, conversation: { session_id: "session", head_id: null }, children: [], ...extra });
  globalThis.runEvents = { a: [agent({})] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    assert.deepEqual(globalThis.activityEventReads, ["a"]);
    globalThis.runEvents = { a: [agent({ ended_at: now - 100, status: "errored", failed: true, error: "boom", closed_as: "errored" })] };
    await act(async () => { window.dispatchEvent(new Event("op:job-status")); });
    await settle();
    assert.deepEqual(globalThis.activityEventReads, ["a", "a"]);
    assert.match(host.textContent, /BackgroundFailed/);
    await act(async () => { window.dispatchEvent(new Event("op:job-status")); });
    await settle();
    assert.deepEqual(globalThis.activityEventReads, ["a", "a"]);
  });
});

test("a run keeps its row when its reply arrives and its id changes", async () => {
  listRuns([activityRun("exec_1", now - 30, { message_id: null, execution_id: "exec_1", reply: "", ended_at: null, execution_status: "running" })]);
  globalThis.runEvents = { exec_1: [asked(now - 30)], reply_1: [asked(now - 30), replied(now - 1)] };
  await mounted([run("exec_1", "running", now - 30)], async (host, click) => {
    await openMain(host, click);
    assert.deepEqual(expanded(host), ["exec_1"]);
    listRuns([activityRun("reply_1", now - 30, { execution_id: "exec_1", ended_at: now, execution_status: "completed" })]);
    await act(async () => { window.dispatchEvent(new Event("op:execution-update")); });
    await settle();
    assert.deepEqual(expanded(host), ["exec_1"]);
    assert.match(host.textContent, /Reply reply_1/);
  });
});

test("on the overview's chart a step, a message and a sub-agent open to their details in place", async () => {
  listRuns([activityRun("a", now - 300, { steps: [step(now - 290, "npm test"), started("j_done", "Research", now - 280, now - 270, { result: "Found it" })] })]);
  await mounted([run("a", "completed", now - 300, { terminal_at: now - 260 }), helper("j_done", "completed", "Research")], async host => {
    await settle();
    // No page is open: the chart is the overview's own.
    assert.ok(!host.querySelector("[data-timeline]"));
    await act(async () => row(host, "Ran npm test").click());
    await settle();
    assert.match(host.textContent, /Input.*npm test.*Result.*output/s);
    await act(async () => row(host, "Research").click());
    await settle();
    assert.match(host.textContent, /Asked.*Brief of Research.*Reply.*Found it/s);
    assert.ok(!host.querySelector("[data-timeline]"));
  });
});

test("steps read in the chat's words, open to input and result, and failed ones say so first", async () => {
  listRuns([activityRun("a", now - 300)]);
  globalThis.runEvents = { a: [step(now - 290, "npm test"), step(now - 280, "ls", { failed: true })] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    assert.match(host.textContent, /Ran npm test.*Failed · Ran ls/s);
    await click("Ran npm test");
    assert.match(host.textContent, /Input.*npm test.*Result.*output/s);
  });
});

test("attempts show the stored failure text, or the reason code when none was stored", async () => {
  listRuns([activityRun("a", now - 300)]);
  const attempt = (number, extra) => ({ kind: "attempt", at: now - 290 + number, number, ended_at: now - 280, status: "ended", outcome: "failed", reason_code: "agent_runner_error", failure_text: null, checkpoint_id: null, ...extra });
  globalThis.runEvents = { a: [attempt(1, { outcome: "restart_recoverable", reason_code: null, checkpoint_id: "ckpt" }), attempt(2, { failure_text: "RuntimeError: boom" }), attempt(3, {})] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    assert.match(host.textContent, /Paused · Attempt 1.*Failed · Attempt 2.*Failed · Attempt 3/s);
    await click("Attempt 1");
    assert.match(host.textContent, /Saved point.*ckpt/s);
    await click("Attempt 2");
    assert.match(host.textContent, /RuntimeError: boom/);
    await click("Attempt 3");
    assert.match(host.textContent, /Reason code.*agent_runner_error/s);
  });
});

test("a failed attempt opens to its error or code alone, a paused one to where it stopped", async () => {
  listRuns([activityRun("a", now - 300)]);
  const attempt = (number, extra) => ({ kind: "attempt", at: now - 290 + number, number, ended_at: now - 280, status: "ended", outcome: "failed", reason_code: "agent_runner_error", failure_text: null, checkpoint_id: "ckpt", ...extra });
  globalThis.runEvents = { a: [attempt(1, { outcome: "waiting_at_safe_point" }), attempt(2, { failure_text: "RuntimeError: boom" })] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    assert.doesNotMatch(host.textContent, /Failed · Attempt 1/);
    await click("Attempt 1");
    assert.match(host.textContent, /Outcome.*waiting_at_safe_point.*Saved point.*ckpt/s);
    assert.doesNotMatch(host.textContent, /Reason code/);
    await click("Attempt 1");
    await click("Attempt 2");
    assert.match(host.textContent, /Error.*RuntimeError: boom/s);
    assert.doesNotMatch(host.textContent, /Reason code|Outcome|Saved point/);
  });
});

test("one attempt that went fine adds no row; with more than one each is listed", async () => {
  const attempt = (number, outcome) => ({ kind: "attempt", at: now - 290 + number, number, ended_at: now - 280, status: "ended", outcome, reason_code: null, failure_text: null, checkpoint_id: null });
  listRuns([activityRun("a", now - 300), activityRun("b", now - 600)]);
  globalThis.runEvents = { a: [asked(now - 300), attempt(1, "completed"), replied(now - 280)], b: [attempt(1, "restart_recoverable"), attempt(2, "completed")] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    assert.match(host.textContent, /Asked: the question/);
    assert.doesNotMatch(host.textContent, /Attempt/);
    await click("Reply b");
    assert.match(host.textContent, /Paused · Attempt 1.*Completed · Attempt 2/s);
  });
});

test("sub-agents nest in their run with their conversation and details; one that never started says so", async () => {
  listRuns([activityRun("a", now - 300)]);
  const agent = (extra) => ({ kind: "subagent", at: now - 290, ended_at: now - 228, job_id: "j_one", name: "Research_notes", agent_id: "butler", context: "clean", status: "completed", never_started: false, failed: false,
    brief: "look it up", brief_truncated: false, result: "found it", result_truncated: false, error: null, created_at: now - 290, closed_as: "completed", conversation: { session_id: "session", head_id: "h" }, children: [], ...extra });
  globalThis.agentTip = "agent_tip";
  globalThis.runEvents = { a: [agent({}), agent({ job_id: null, name: "Broken", never_started: true, failed: true, ended_at: null, error: "admission is missing", conversation: null, agent_id: null, context: null, closed_as: null, result: null })] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    assert.match(host.textContent, /Research notes1m 2s/);
    assert.match(host.textContent, /BrokenFailed · never started/);
    await click("Research notes");
    assert.match(host.textContent, /Asked: look it up.*Replied: found it.*Open its conversation.*Technical details/s);
    await click("Open its conversation");
    await settle();
    assert.deepEqual(globalThis.sentCommands.map(c => [c.action, c.head_msg_id]), [["checkout_branch", "agent_tip"], ["load_session", undefined]]);
    await click("Broken");
    assert.match(host.textContent, /Failed · admission is missing/);
  });
});

test("a sub-agent still running can already be opened in its conversation", async () => {
  listRuns([activityRun("a", now - 300)]);
  globalThis.agentTip = "live_tip";
  globalThis.runEvents = { a: [{ kind: "subagent", at: now - 290, ended_at: null, job_id: "j_live", name: "Live", agent_id: null, context: null, status: "running", never_started: false, failed: false,
    brief: "go", brief_truncated: false, result: null, result_truncated: false, error: null, created_at: now - 290, closed_as: null, conversation: { session_id: "session", head_id: null }, children: [] }] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await click("Reply a");
    await click("Live");
    await click("Open its conversation");
    await settle();
    assert.deepEqual(globalThis.sentCommands.map(c => [c.action, c.head_msg_id]), [["checkout_branch", "live_tip"], ["load_session", undefined]]);
  });
});

test("action rows draw line icons, not filled ones", async () => {
  listRuns([activityRun("a", now - 300, { execution_id: "turn", execution_status: "running", ended_at: null })]);
  const live = run("turn", "running", now - 300, { capabilities: { pause: true, steer: false, cancel: false }, run_id: "run_1", revision_id: "rev_1" });
  await mounted([live], async (host, click) => {
    await openMain(host, click);
    await settle();
    const pause = row(host, "Pause");
    assert.ok(pause, "Pause row");
    const icon = pause.querySelector("svg");
    assert.equal(icon.getAttribute("stroke-width"), "1.8");
    assert.equal(icon.getAttribute("width"), "20");
    assert.equal(icon.getAttribute("fill"), "none");
  });
});

test("a waiting run opens itself, shows the question last and goes to the chat's card", async () => {
  listRuns([activityRun("a", now - 60, { execution_id: "turn", reply: "", ended_at: null, execution_status: "paused" })]);
  globalThis.runEvents = { a: [asked(now - 60)] };
  useSessionStore.setState({ pendingDecisions: [{ id: "d1", sessionId: "session", executionId: "turn", kind: "question", prompt: "Which one?", options: ["left", "right"] }] });
  try {
    await mounted([run("turn", "paused", now - 60)], async (host, click) => {
      await openMain(host, click);
      await settle();
      assert.match(host.textContent, /Which one\?Waiting for your answer/);
      assert.match(host.textContent, /Answer in chat/);
      await click("Which one?");
      assert.match(host.textContent, /Choices.*left · right/s);
    });
  } finally { useSessionStore.setState({ pendingDecisions: [] }); }
});

test("controls come from what the run allows, and are off, without a word, until the runs are read", async () => {
  const live = run("turn", "running", now - 60, { capabilities: { pause: true, steer: true, cancel: true, step: false, retry: false, fork: false }, run_id: "run_1", revision_id: "rev_1" });
  listRuns([activityRun("a", now - 60, { execution_id: "turn", reply: "", ended_at: null, execution_status: "running" })]);
  await mounted([live], async (host, click) => {
    await openMain(host, click);
    await settle();
    assert.match(host.textContent, /PauseHolds at the next safe point/);
    assert.match(host.textContent, /Add instruction/);
    await click("Pause");
    assert.equal(globalThis.commands[0].action, "execution.pause");
    await click("Technical details");
    assert.match(host.textContent, /Execution ID.*turn/s);
  });
  await mounted([live], async (host, click) => {
    await openMain(host, click);
    await settle();
    assert.match(host.textContent, /PauseHolds at the next safe point/);
    assert.doesNotMatch(host.textContent, /Not connected/);
    assert.equal([...host.querySelectorAll("button")].find(button => button.textContent.startsWith("Pause")).disabled, true);
    assert.equal(host.querySelectorAll("[title]").length, 0);
  }, [], { connection: { state: "reconnecting" } });
});

test("a failed run has stopped: no Retry, and Create branch in the details", async () => {
  const failed = run("turn", "failed", now - 60, { checkpoint_head_id: "ckpt_1", capabilities: { retry: true, fork: true, pause: false, step: false, steer: false }, run_id: "run_1", revision_id: "rev_1" });
  listRuns([activityRun("a", now - 60, { execution_id: "turn", execution_status: "failed" })]);
  const at = now - 30;
  const stamp = new Date(at * 1000).toTimeString().slice(0, 8);
  globalThis.runEvents = { a: [{ kind: "attempt", at, number: 1, ended_at: at, status: "ended", outcome: "restart_recoverable", reason_code: null, failure_text: null, checkpoint_id: "ckpt_1" }] };
  await mounted([failed], async (host, click) => {
    await openMain(host, click);
    await settle();
    assert.doesNotMatch(host.textContent, /Retry/);
    await click("Technical details");
    assert.match(host.textContent, new RegExp(`Create branchTry a change from ${stamp}`));
  });
});

test("Esc goes back only from inside the panel, Activity in the rail goes back, and level 1 returns", async () => {
  listRuns([activityRun("a", now - 300)]);
  await mounted([], async (host, click, selected, render) => {
    // The overview's chart names the run too, so the page is told by its Back.
    const onPage = () => host.querySelector("[data-timeline]") !== null;
    await openMain(host, click);
    assert.ok(onPage());
    await key(document.body, "Escape");
    assert.ok(onPage());
    const input = document.createElement("input");
    host.querySelector("[data-timeline] [data-row-id]").closest("div").append(input);
    await key(input, "Escape");
    assert.ok(onPage());
    await key([...host.querySelectorAll("[data-timeline] [data-row-id]")].find(item => item.textContent.includes("Reply a")), "Escape");
    assert.ok(!onPage());
    assert.ok(glyph(host, "Main agent"));
    await openMain(host, click);
    assert.ok(onPage());
    await render({ resetSignal: 1 });
    assert.ok(!onPage());
  }, [], {}, { calls: [{ id: "call", name: "bash", command: "sleep 30", started_at: now - 5, status: "running" }] });
});

test("the overview charts the runs: the groups have the panel until there is one, the chart has one Tab stop, and it is still there after a page", async () => {
  const groups = host => host.querySelector("section > div");
  listRuns([]);
  await mounted([], async host => {
    await settle();
    assert.equal(host.querySelector("[data-chart-column]"), null);
    assert.match(groups(host).className, /flex-1/);
    assert.doesNotMatch(groups(host).className, /max-h/);
  });
  let reads = 0;
  const topic = { topic_id: "t1", topic_title: "Release", topic_order: 0 };
  const runs = [activityRun("a", now - 300, { ...topic, steps: [step(now - 290, "ls", { task: "T1" })] }), activityRun("b", now - 600, topic)];
  globalThis.activityRuns = async (session, query) => { if (query.chart) reads += 1; return { runs, offset: 0, total: runs.length }; };
  await mounted([], async (host, click) => {
    await settle();
    assert.match(groups(host).className, /max-h-\[50%\]/);
    const chart = host.querySelector("[data-chart-column]").parentElement;
    assert.equal(chart.querySelectorAll('[data-row-id][tabindex="0"]').length, 1);
    // A call made for a task puts the task in the first column.
    assert.match(host.querySelector('[data-chart-column="0"]').textContent, /2 messages · 1 task/);
    // A block far from the view leaves the layout; the Tab stop does not stay on it.
    const [held, next] = chart.querySelectorAll("[data-row-id]");
    assert.equal(held.getAttribute("tabindex"), "0");
    await act(async () => { held.setAttribute("style", "display: none"); await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.deepEqual([held.getAttribute("tabindex"), next.getAttribute("tabindex")], ["-1", "0"]);
    await act(async () => { held.removeAttribute("style"); await new Promise(resolve => setTimeout(resolve, 0)); });
    // Esc on the first column has nowhere to go and is left alone.
    const escape = new Event("keydown", { bubbles: true, cancelable: true });
    Object.assign(escape, { key: "Escape" });
    await act(async () => { chart.querySelector("[data-row-id]").dispatchEvent(escape); });
    assert.equal(escape.defaultPrevented, false);
    // A page over the overview and back: the chart is drawn from what was read, before any new read lands.
    await openMain(host, click);
    const before = reads;
    await key(host.querySelector("[data-timeline] [data-row-id]"), "Escape");
    assert.ok(host.querySelector('[data-chart-column="0"]'));
    assert.match(host.querySelector('[data-chart-column="0"]').textContent, /2 messages · 1 task/);
    assert.ok(reads - before <= 1);
  });
});

test("the arrows pass over a row that is out of the layout", async () => {
  listRuns([activityRun("a", now - 300), activityRun("b", now - 600), activityRun("c", now - 900)]);
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await settle();
    const [first, second, third] = ["Reply a", "Reply b", "Reply c"].map(name => row(host, name));
    second.style.display = "none";
    await key(first, "ArrowDown");
    assert.equal(document.activeElement, third);
    await key(third, "ArrowUp");
    assert.equal(document.activeElement, first);
    await key(first, "End");
    assert.equal(document.activeElement, third);
  });
});

test("the keyboard moves among rows with one Tab stop and opens with right, closes with left", async () => {
  listRuns([activityRun("a", now - 300), activityRun("b", now - 600)]);
  globalThis.runEvents = { a: [asked(now - 300)], b: [] };
  await mounted(finishedTurn, async (host, click) => {
    await openMain(host, click);
    await settle();
    const first = row(host, "Reply a");
    assert.equal(host.querySelectorAll('[data-row-id][tabindex="0"]').length, 1);
    await key(first, "ArrowDown");
    const second = row(host, "Reply b");
    assert.equal(document.activeElement, second);
    assert.equal(host.querySelectorAll('[data-row-id][tabindex="0"]').length, 1);
    await key(second, "Home");
    assert.equal(document.activeElement, first);
    await key(first, "ArrowRight");
    assert.deepEqual(expanded(host), ["a"]);
    await key(first, "ArrowLeft");
    assert.deepEqual(expanded(host), []);
  });
});

test("the chart draws the reader's branches, each main agent with its glyph; runtime-opened ones are none", async () => {
  const state = globalThis.runtimeStateStub ||= { _branchesByConv: {}, _branchLaneColorMap: {} };
  const runtime = Array.from({ length: 12 }, (_, index) => ({ head_msg_id: `goal_${index}`, branch_id: `auto_${index}`, name: "Survey check", updated_at: now - 600 * index, runtime_started: true }));
  const byHead = { tip: [activityRun("mine", now - 100), activityRun("shared", now - 900)], old: [activityRun("theirs", now - 500), activityRun("shared", now - 900)], older: [activityRun("shared", now - 900)] };
  try {
    // A Goal conversation saved while each runtime turn was recorded as a branch: one agent, no fork.
    state._branchesByConv.session = [{ head_msg_id: "tip", branch_id: "current", name: "Survey check", active: true, updated_at: now, runtime_started: true }, ...runtime];
    await mounted([], async host => {
      await settle();
      assert.match(host.textContent, /^Main agent/);
      assert.doesNotMatch(host.textContent, /Survey check|other branch/);
    });
    // Branches the reader made: each that has a message of its own wears its glyph on the newest one.
    state._branchesByConv.session = [{ head_msg_id: "tip", branch_id: "current", name: "Rewrite", active: true, updated_at: now },
      { head_msg_id: "old", branch_id: "first", name: "First try", updated_at: now - 900 }, { head_msg_id: "older", branch_id: "second", name: "Second try", updated_at: now - 1800 }, ...runtime];
    globalThis.activityRuns = async (session, query) => ({ runs: byHead[query.head ?? "tip"] ?? [], offset: 0, total: 2 });
    await mounted([], async host => {
      await settle();
      const marks = [...host.querySelectorAll("button[aria-label]")].filter(button => !button.hasAttribute("data-chart-back")).map(button => button.getAttribute("aria-label"));
      // The run every branch shares is the checked-out branch's, so a branch with nothing of its own has no glyph.
      assert.deepEqual(marks, ["Rewrite · Current", "First try"]);
      assert.doesNotMatch(host.textContent, /Survey check|other branch/);
      assert.equal(host.querySelector('[data-chart-column="1"]').querySelectorAll("[data-chart-id]").length, 3);
    });
  } finally { delete state._branchesByConv.session; }
});
