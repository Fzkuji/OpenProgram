import assert from "node:assert/strict";
import test from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

const ICON_NAMES = "export const ActivityIcon=()=>null;export const ArrowUpRightIcon=()=>null;export const AtomIcon=()=>null;export const BlocksIcon=()=>null;export const BookTextIcon=()=>null;export const BotIcon=()=>null;export const BoxIcon=()=>null;export const BoxesIcon=()=>null;export const BrainIcon=()=>null;export const CalendarDaysIcon=()=>null;export const ChartColumnIncreasingIcon=()=>null;export const CheckIcon=()=>null;export const ChevronDownIcon=()=>null;export const ChevronRightIcon=()=>null;export const ChevronsUpDownIcon=()=>null;export const ChromeIcon=()=>null;export const CircleHelpIcon=()=>null;export const ClockIcon=()=>null;export const CompassIcon=()=>null;export const CopyIcon=()=>null;export const QuoteIcon=()=>null;export const CpuIcon=()=>null;export const CursorClickIcon=()=>null;export const EarthIcon=()=>null;export const EyeIcon=()=>null;export const FeatherIcon=()=>null;export const FileTextIcon=()=>null;export const FingerprintIcon=()=>null;export const FlameIcon=()=>null;export const FolderCodeIcon=()=>null;export const FolderOpenIcon=()=>null;export const FoldersIcon=()=>null;export const GalleryVerticalEndIcon=()=>null;export const GaugeIcon=()=>null;export const GitBranchIcon=()=>null;export const GraduationCapIcon=()=>null;export const HammerIcon=()=>null;export const HistoryIcon=()=>null;export const KeyIcon=()=>null;export const LanguagesIcon=()=>null;export const MessageCircleIcon=()=>null;export const MicIcon=()=>null;export const MonitorIcon=()=>null;export const PanelLeftCloseIcon=()=>null;export const PanelLeftOpenIcon=()=>null;export const PenToolIcon=()=>null;export const BookmarkIcon=()=>null;export const PlugZapIcon=()=>null;export const PlusIcon=()=>null;export const RefreshCwIcon=()=>null;export const RocketIcon=()=>null;export const RouteIcon=()=>null;export const SatelliteDishIcon=()=>null;export const ScanTextIcon=()=>null;export const SearchIcon=()=>null;export const SettingsIcon=()=>null;export const ShieldCheckIcon=()=>null;export const SparklesIcon=()=>null;export const SquarePenIcon=()=>null;export const TelescopeIcon=()=>null;export const TimerIcon=()=>null;export const UndoIcon=()=>null;export const WorkflowIcon=()=>null;export const XIcon=()=>null;export const ZapIcon=()=>null;";
installLoader({
  cssModules: "empty",
  stubs: { "@/components/animated-icons": ICON_NAMES },
  // Only presentation wrappers are simplified; GoalChip, hooks, API and
  // shared session cache are the production modules under test.
  replace: {
    "/components/ui/dialog.tsx": `export const Dialog = ({open, children}) => open ? children : null;
      export const DialogContent = ({children}) => children;
      export const DialogDescription = DialogContent;
      export const DialogFooter = DialogContent;
      export const DialogHeader = DialogContent;
      export const DialogTitle = DialogContent;`,
  },
});
// The real i18n module answers in English through the stored locale.
installDom({ pathname: "/chat" });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { GoalChip, GoalDetails } = await import("../../components/chat/badges/goal-chip.tsx");
const { runtimeState, setSocket } = await import("../../lib/runtime-bridge/state.ts");
const { updateStatus } = await import("../../lib/runtime-bridge/ui.ts");
const { useSessionStore } = await import("../../lib/session-store/index.ts");
const { api } = await import("../../lib/net/api.ts");

const snapshot = (version, status = "active") => ({
  goal_id: "goal-1", version, revision: 1, text: "Write the review", status,
});
const controls = (overrides = {}) => ({
  can_resume: true, can_verify: false, can_end: true, can_pause: false,
  reasons: { resume: null, verify: "no_completed_work", end: null, pause: "goal_not_running" },
  operations: [], waits: [], active_children: [], ...overrides,
});

test("backend eligibility overrides a finished execution and explains why", async () => {
  reset(); useSessionStore.setState({ wsStatus: "open" });
  const goal = { ...snapshot(1, "paused"), execution_id: "finished" };
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal;
  api.getGoal = async () => ({ goal, execution: { execution_id: "finished", finished: true },
    controls: controls({ can_resume: false, reasons: { resume: "active_owner" } }) });
  const view = await mount();
  try {
    await view.open();
    assert.equal([...view.host.querySelectorAll("button")].find(b => b.textContent === "Resume").disabled, true);
    assert.match(view.host.textContent, /Another turn is running/);
  } finally { await view.close(); api.getGoal = original; }
});

test("untracked Goal reads backend controls and explicit Verify uses the ordinary action", async () => {
  reset(); useSessionStore.setState({ wsStatus: "open" });
  const goal = snapshot(1, "paused");
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal, mutate = api.mutateGoal;
  let reads = 0, sent;
  api.getGoal = async () => { reads++; return { goal, execution: { execution_id: null, status: "untracked" },
    controls: controls({ can_verify: true, reasons: {} }) }; };
  api.mutateGoal = async (_sid, body) => { sent = body; return { goal: { ...goal, version: 2, status: "active", phase: "verifying" } }; };
  const view = await mount();
  try {
    await view.open(); assert.ok(reads > 0);
    await view.click("Verify");
    assert.equal(sent.action, "verify");
    assert.equal(sent.expected.goal_id, goal.goal_id);
    assert.equal(runtimeState.conversations.s1.goal.status, "active");
  } finally { await view.close(); api.getGoal = original; api.mutateGoal = mutate; }
});

test("unknown operations expose exact identity without inventing a cancelled outcome", async () => {
  reset(); useSessionStore.setState({ wsStatus: "open" });
  const goal = { ...snapshot(1, "paused"), execution_id: "old" };
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal;
  api.getGoal = async () => ({ goal, execution: { execution_id: "old", status: "reconciliation_required" },
    controls: controls({ operations: [{ effect_id: "effect-exact-123", execution_id: "old", status: "dispatched", tool_name: "write", created_at: 100 }] }) });
  const view = await mount();
  try {
    await view.open();
    assert.match(view.host.textContent, /effect-exact-123/);
    assert.match(view.host.textContent, /dispatched/);
    assert.ok([...view.host.querySelectorAll("button")].some(b => b.textContent === "Inspect execution"));
    assert.doesNotMatch(view.host.textContent, /Execution stopped/);
  } finally { await view.close(); api.getGoal = original; }
});

test("refresh adopts a newer server run instead of retaining disabled stale controls", async () => {
  reset();
  const old = { ...snapshot(1, "paused"), run_id: "old", execution_id: "old-exec" };
  const latest = { ...old, version: 2, run_id: "new", execution_id: "new-exec" };
  runtimeState.conversations.s1.goal = old;
  const original = api.getGoal;
  api.getGoal = async () => ({ goal: latest, controls: controls(), execution: { execution_id: "new-exec", status: "cancelled", finished: true } });
  const view = await mount();
  try {
    await view.open();
    assert.equal(runtimeState.conversations.s1.goal.run_id, "new");
    assert.equal([...view.host.querySelectorAll("button")].find(b => b.textContent === "Resume").disabled, false);
  } finally { await view.close(); api.getGoal = original; }
});

test("Inspect execution requests the exact old operation, not the newest execution", async () => {
  reset();
  const goal = { ...snapshot(1, "paused"), execution_id: "old-exec" };
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal, fetch = globalThis.fetch;
  api.getGoal = async () => ({ goal, controls: controls(), execution: { execution_id: "old-exec", status: "reconciliation_required" } });
  const urls = [];
  const old = { execution_id: "old-exec", session_id: "s1", run_id: "run", revision_id: "rev", status: "reconciliation_required", status_version: 3,
    updated_at: 1, event_sequence: 3, capabilities: {}, pending_command_ids: [], active_child_ids: [], effect_summary: { unresolved: 1 } };
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    const body = String(url).endsWith("/executions") ? { items: [{ snapshot: old }, { snapshot: { ...old, execution_id: "new-exec", updated_at: 99 } }] }
      : String(url).includes("/events?") ? { snapshot: old, events: [] }
      : { checkpoints: [], waits: [], drafts: [], unresolved_effects: [] };
    return { ok: true, status: 200, json: async () => body };
  };
  const view = await mount();
  try {
    await view.open(); await view.click("Inspect execution");
    assert.ok(urls.includes("/api/execution/old-exec/debugger?conversation_session_id=s1"), urls);
    assert.ok(!urls.some(url => url.includes("/new-exec/")), urls);
    await view.click("Close execution details");
  } finally { await view.close(); api.getGoal = original; globalThis.fetch = fetch; }
});
test("historical Goal reuses details without reading or editing the current Goal", async () => {
  reset();
  const archived = { ...snapshot(3, "achieved"), text: "Archived objective", usage: { cost_known: true, cost_usd: 0.25 } };
  runtimeState.conversations.s1.goal = { ...snapshot(4), goal_id: "new", text: "New objective" };
  let reads = 0;
  api.getGoal = async () => { reads++; throw new Error("historical record must not query current Goal"); };
  const view = await mount(createElement(GoalDetails, { sessionId: "s1", goal: archived, historical: true }));
  try {
    assert.equal(view.host.querySelectorAll("button[data-goal-id]").length, 1);
    await view.open();
    assert.equal(view.host.querySelector("textarea").value, "Archived objective");
    assert.ok(view.host.querySelector("textarea").hasAttribute("readonly"));
    assert.ok(!view.host.textContent.includes("New objective"));
    assert.ok(!view.host.textContent.includes("Save edit"));
    assert.ok(!view.host.textContent.includes("Stop not confirmed"));
    assert.equal(reads, 0);
  } finally { await view.close(); }
});

async function mount(element = createElement(GoalChip)) {
  const view = await render(element);
  const host = view.container;
  return {
    host,
    async click(text) {
      const button = [...host.querySelectorAll("button")].find(item => item.textContent === text);
      assert.ok(button, `missing button ${text}: ${host.textContent}`);
      await act(async () => button.dispatchEvent(new Event("click", { bubbles: true })));
    },
    async open() { await act(async () => host.querySelector("button").click()); },
    close: () => view.unmount(),
  };
}
async function frame(goal, sid = "s1") {
  await act(async () => window.dispatchEvent(new CustomEvent("op:ws-message", {
    detail: { type: "goal_update", data: { session_id: sid, goal } },
  })));
}
function reset() {
  runtimeState.conversations = { s1: { id: "s1", goal: snapshot(1) } };
  useSessionStore.setState({ currentSessionId: "s1", wsStatus: "open" });
  // Default HTTP fixture describes an untracked Goal; action-specific tests
  // replace it with the corresponding canonical projection.
  api.getGoal = async (sid) => {
    const goal = runtimeState.conversations[sid].goal;
    return { goal, execution: { execution_id: goal.execution_id ?? null, status: "untracked" },
      controls: controls({ can_resume: goal.status === "paused", can_pause: goal.status === "active" }) };
  };
}

test("Goal progress shows todos, never execution rounds", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1), turns_used: 12, max_turns: 150 };
  const view = await mount();
  try {
    assert.equal(view.host.querySelector(".badge-short").textContent, "Goal · Active");
    assert.doesNotMatch(view.host.textContent, /12\/150/);
    await frame({ ...snapshot(2), checklist: [{ text: "Verify", done: false }] });
    assert.match(view.host.textContent, /Todos 0\/1/);
    await frame({ ...snapshot(3), checklist: [{ text: "Verify", done: true }] });
    assert.match(view.host.textContent, /Todos 1\/1/);
    await frame({ ...snapshot(4, "paused_recoverable"), checklist: [] });
    assert.equal(view.host.querySelector(".badge-short").textContent, "Goal · Paused");
    await view.open();
    assert.doesNotMatch(view.host.textContent, /No todos yet|尚无待办|Progress/);
  } finally { await view.close(); }
});

test("completed todo progress remains Verifying until evidence passes", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1), phase: "verifying", checklist: [{ text: "Verify", done: true }] };
  const view = await mount();
  try {
    assert.equal(view.host.querySelector(".badge-short").textContent, "Goal · Verifying · Todos 1/1");
    await frame({ ...snapshot(2, "achieved"), phase: "terminal" });
    assert.equal(view.host.querySelector(".badge-short"), null);
  } finally { await view.close(); }
});

test("partial usage displays known subtotal and unknown request counts", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "paused"), usage: {
    total_tokens: 120, tokens_known: false, unknown_token_requests: 1,
    cost_usd: 0.42, cost_known: false, unknown_cost_requests: 2,
  } };
  const view = await mount();
  try {
    await view.open();
    assert.match(view.host.textContent, /Known 120; 1 request\(s\) unknown/);
    assert.match(view.host.textContent, /Known \$0.4200; 2 request\(s\) unknown/);
  } finally { await view.close(); }
});

test("provider-only incomplete responses allow a new Goal turn without claiming stop", async () => {
  reset();
  useSessionStore.setState({ wsStatus: "open" });
  const goal = { ...snapshot(1, "paused_recoverable"), execution_id: "old-provider" };
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal;
  api.getGoal = async () => ({ goal, controls: controls(), execution: {
    execution_id: goal.execution_id, status: "reconciliation_required",
    finished: false, can_start_new_turn: true, provider_response_incomplete: true,
  } });
  const view = await mount();
  try {
    await view.open();
    const resume = [...view.host.querySelectorAll("button")].find(b => b.textContent === "Resume");
    assert.equal(resume.disabled, false);
    assert.match(view.host.textContent, /Previous model response is incomplete/);
    assert.doesNotMatch(view.host.textContent, /Execution stopped/);
  } finally { await view.close(); api.getGoal = original; }
});

test("unknown external effects permit restricted Resume without claiming cancellation", async () => {
  reset();
  useSessionStore.setState({ wsStatus: "open" });
  const goal = { ...snapshot(1, "paused_recoverable"), execution_id: "unknown-write", stop_requested: true };
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal;
  api.getGoal = async () => ({ goal, controls: controls(), execution: {
    execution_id: goal.execution_id, status: "reconciliation_required", finished: false,
    can_start_new_turn: true, recovery_mode: "restricted_new_turn",
  } });
  const view = await mount();
  try {
    await view.open();
    const resume = [...view.host.querySelectorAll("button")].find(b => b.textContent === "Resume");
    assert.equal(resume.disabled, false);
    assert.match(view.host.textContent, /new side effects require confirmation/);
    assert.doesNotMatch(view.host.textContent, /Execution stopped|Resume is disabled/);
  } finally { await view.close(); api.getGoal = original; }
});

test("real connection updates enable Goal resume only after a fresh stop observation", async () => {
  reset();
  const previousWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 };
  const socket = { readyState: WebSocket.CONNECTING };
  setSocket(socket);
  updateStatus("connecting");
  const goal = { ...snapshot(2, "paused"), execution_id: "exec-resume", stop_requested: true };
  runtimeState.conversations.s1.goal = goal;
  const original = api.getGoal;
  let reads = 0;
  api.getGoal = async () => {
    reads++;
    return { goal, controls: controls(), execution: { execution_id: "exec-resume", status: "cancelled", finished: true } };
  };
  const view = await mount();
  try {
    await view.open();
    const resume = () => [...view.host.querySelectorAll("button")].find(b => b.textContent === "Resume");
    assert.equal(resume().disabled, true);
    await act(async () => { socket.readyState = WebSocket.OPEN; updateStatus("connected"); });
    assert.equal(useSessionStore.getState().wsStatus, "open");
    assert.equal(resume().disabled, false);
    const connectedReads = reads;
    await act(async () => { socket.readyState = WebSocket.CLOSED; updateStatus("disconnected"); });
    assert.equal(resume().disabled, true);
    assert.match(view.host.textContent, /Execution status unknown/);
    // A source/badge refresh must not report a disconnected socket as open.
    await act(async () => updateStatus("connected", "Local"));
    assert.equal(useSessionStore.getState().wsStatus, "closed");
    await act(async () => { socket.readyState = WebSocket.OPEN; updateStatus("connected"); });
    assert.ok(reads > connectedReads);
    assert.equal(resume().disabled, false);
  } finally {
    await view.close();
    api.getGoal = original;
    setSocket(null);
    updateStatus("disconnected");
    globalThis.WebSocket = previousWebSocket;
  }
});

test("a cancelled Goal keeps stop controls until canonical termination is confirmed", async () => {
  reset();
  useSessionStore.setState({ wsStatus: "open" });
  const goal = { ...snapshot(2, "cancelled"), execution_id: "exec-stop", stop_requested: true };
  runtimeState.conversations.s1.goal = goal;
  const originalGet = api.getGoal, originalMutate = api.mutateGoal;
  let finished = false, action;
  api.getGoal = async () => ({ goal, execution: { execution_id: "exec-stop", status: finished ? "cancelled" : "running", finished } });
  api.mutateGoal = async (_sid, body) => {
    action = body.action;
    return { goal, stop_error: "Goal change saved. Execution stop is not confirmed." };
  };
  const view = await mount();
  try {
    assert.match(view.host.textContent, /Stop not confirmed/);
    await view.open();
    await view.click("Retry stop");
    assert.equal(action, "stop");
    assert.match(view.host.textContent, /Goal change saved/);
    finished = true;
    await act(async () => window.dispatchEvent(new CustomEvent("op:execution-update", {
      detail: { execution: { execution_id: "exec-stop", session_id: "s1", status: "cancelled" } },
    })));
    assert.match(view.host.textContent, /Execution stopped/);
    assert.equal(view.host.querySelector('[aria-label="Open Goal details"]'), null);
  } finally {
    await view.close(); api.getGoal = originalGet; api.mutateGoal = originalMutate;
    useSessionStore.setState({ wsStatus: "connecting" });
  }
});

test("late stop observations cannot enable a newer run and offline state stays unknown", async () => {
  reset(); useSessionStore.setState({ wsStatus: "open" });
  const oldGoal = { ...snapshot(2, "paused"), run_id: "old", execution_id: "old-exec", stop_requested: true };
  const nextGoal = { ...oldGoal, version: 3, run_id: "new", execution_id: "new-exec" };
  runtimeState.conversations.s1.goal = oldGoal;
  const original = api.getGoal;
  let finishOld;
  api.getGoal = async () => new Promise((resolve) => { finishOld = resolve; });
  const view = await mount();
  try {
    await view.open();
    const resolveOld = finishOld;
    api.getGoal = async () => ({ goal: nextGoal, execution: { execution_id: "new-exec", status: "running", finished: false } });
    await frame(nextGoal);
    await act(async () => resolveOld({ goal: oldGoal, execution: { execution_id: "old-exec", status: "cancelled", finished: true } }));
    const resume = [...view.host.querySelectorAll("button")].find((b) => b.textContent === "Resume");
    assert.equal(resume.disabled, true);
    assert.match(view.host.textContent, /Stop not confirmed/);
    await act(async () => useSessionStore.setState({ wsStatus: "closed" }));
    assert.match(view.host.textContent, /Execution status unknown/);
    assert.ok(view.host.querySelector('[aria-label="Open Goal details"]'));
  } finally { await view.close(); api.getGoal = original; useSessionStore.setState({ wsStatus: "connecting" }); }
});

async function typeInto(node, value) {
  const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value");
  descriptor.set.call(node, value);
  await act(async () => node.dispatchEvent(new Event("input", { bubbles: true })));
}

test("Goal actions include the snapshot identity shown to the user", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(3), run_id: "run-1" };
  const original = api.mutateGoal;
  let sent;
  api.mutateGoal = async (_sid, body) => { sent = body; return { goal: snapshot(4, "paused") }; };
  const view = await mount();
  try {
    await view.open();
    await view.click("Pause");
    assert.deepEqual(sent.expected, { goal_id: "goal-1", revision: 1, run_id: "run-1", version: 3 });
  } finally { api.mutateGoal = original; await view.close(); }
});

test("Goal details have no execution parameter editors", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "paused"), budget: { max_tokens: 5000 } };
  const view = await mount();
  try {
    await view.open();
    assert.equal(view.host.querySelectorAll("input, select").length, 0);
    assert.doesNotMatch(view.host.textContent, /Configure agents|Execution limits|Save roles|Save limits|Save budget/);
    assert.ok(view.host.querySelector('textarea'));
  } finally { await view.close(); }
});

test("usage shows each explicit limit next to it", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "budget_exhausted"),
    budget: { max_tokens: 8000, max_elapsed_s: 600 }, usage: { total_tokens: 1200, active_elapsed_s: 90 } };
  const view = await mount();
  try {
    await view.open();
    assert.match(view.host.textContent, /Tokens1200 \/ 8000/);
    assert.match(view.host.textContent, /1m 30s \/ 10m 0s/);
    assert.doesNotMatch(view.host.textContent, /Turns/);
  } finally { await view.close(); }
});

test("a stopped Goal explains its stop reason instead of a raw code", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "stalled"), pause_reason: "idle",
    last_reason: "2 consecutive automatic turns called no tool" };
  const view = await mount();
  try {
    await view.open();
    assert.match(view.host.textContent, /2 automatic turns in a row called no tool/);
    await frame({ ...snapshot(2, "paused_recoverable"), pause_reason: "plan_mode", last_reason: "plan mode" });
    assert.equal(view.host.querySelector(".badge-short").textContent, "Goal · Paused");
    assert.match(view.host.textContent, /StatusPaused/);
    assert.match(view.host.textContent, /does not continue on its own in Plan mode/);
    await frame({ ...snapshot(3, "paused_recoverable"), pause_reason: "verification_blocked",
      last_reason: "Completion cannot be verified: unfinished_todos" });
    assert.match(view.host.textContent, /Completion cannot be verified yet: Complete the remaining todos/);
  } finally { await view.close(); }
});

test("remote edits retain the local goal draft and require explicit reload", async () => {
  reset();
  const view = await mount();
  try {
    await view.open();
    const input = view.host.querySelector("textarea");
    await typeInto(input, "My unsaved review");
    await frame({ ...snapshot(2), revision: 2, text: "Remote scope" });
    assert.equal(input.value, "My unsaved review");
    assert.match(view.host.textContent, /changed elsewhere/i);
    await view.click("Use latest goal");
    assert.equal(input.value, "Remote scope");
  } finally { await view.close(); }
});

test("switching sessions closes the old Goal dialog and isolates late errors", async () => {
  reset();
  runtimeState.conversations.s2 = { id: "s2", goal: { ...snapshot(1), goal_id: "goal-2", text: "Second goal" } };
  const original = api.mutateGoal;
  let reject;
  api.mutateGoal = () => new Promise((_resolve, failure) => {
    reject = failure;
    setTimeout(() => failure(new Error("Old request failed")), 100);
  });
  const view = await mount();
  try {
    await view.open();
    await view.click("Pause");
    await act(async () => useSessionStore.setState({ currentSessionId: "s2" }));
    assert.equal(view.host.querySelector("textarea"), null);
    await view.open();
    await act(async () => reject(new Error("Old request failed")));
    assert.equal(view.host.querySelector("textarea").value, "Second goal");
    assert.doesNotMatch(view.host.textContent, /Old request failed/);
  } finally { api.mutateGoal = original; await view.close(); }
});

test("unknown cost is not displayed as a zero-dollar charge", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1), usage: { cost_known: false, cost_usd: 0 } };
  const view = await mount();
  try {
    await view.open();
    assert.match(view.host.textContent, /Unknown/);
    assert.doesNotMatch(view.host.textContent, /\$0\.0000/);
  } finally { await view.close(); }
});

test("failed saves keep the draft and duplicate clicks send one request", async () => {
  reset();
  const original = api.mutateGoal;
  let reject;
  let calls = 0;
  api.mutateGoal = () => { calls++; return new Promise((_resolve, failure) => { reject = failure; }); };
  const view = await mount();
  try {
    await view.open();
    await typeInto(view.host.querySelector("textarea"), "Keep this draft");
    const save = [...view.host.querySelectorAll("button")].find(node => node.textContent === "Save edit");
    await act(async () => { save.click(); save.click(); });
    assert.equal(calls, 1);
    await act(async () => reject(new Error("Save conflict")));
    assert.equal(view.host.querySelector("textarea").value, "Keep this draft");
    assert.match(view.host.textContent, /Save conflict/);
  } finally { api.mutateGoal = original; await view.close(); }
});

test("completion keeps an already-open editor and its unsaved text", async () => {
  reset();
  const view = await mount();
  try {
    await view.open();
    await typeInto(view.host.querySelector("textarea"), "A revised goal");
    await frame(snapshot(2, "achieved"));
    assert.equal(view.host.querySelector("textarea").value, "A revised goal");
    assert.equal(view.host.querySelector('[aria-label="Open Goal details"]'), null);
  } finally { await view.close(); }
});

test("a conflict fetches the latest Goal without replacing the local draft", async () => {
  reset();
  const { HttpError } = await import("../../lib/net/fetch-client.ts");
  const mutate = api.mutateGoal;
  const get = api.getGoal;
  let conflicted = false;
  api.mutateGoal = async () => { conflicted = true; throw new HttpError("Goal changed", 409); };
  api.getGoal = async () => ({ goal: conflicted ? { ...snapshot(2), revision: 2, text: "Saved remotely" } : snapshot(1) });
  const view = await mount();
  try {
    await view.open();
    await typeInto(view.host.querySelector("textarea"), "Local draft");
    await view.click("Save edit");
    assert.equal(runtimeState.conversations.s1.goal.text, "Saved remotely");
    assert.equal(view.host.querySelector("textarea").value, "Local draft");
    assert.match(view.host.textContent, /changed elsewhere/);
  } finally { api.mutateGoal = mutate; api.getGoal = get; await view.close(); }
});

test("ending a Goal requires explicit confirmation", async () => {
  reset();
  const mutate = api.mutateGoal;
  let calls = 0;
  api.mutateGoal = async () => { calls++; return { goal: snapshot(2, "cancelled") }; };
  const view = await mount();
  try {
    await view.open();
    await view.click("End");
    assert.equal(calls, 0);
    await view.click("Keep goal");
    assert.equal(calls, 0);
    await view.click("End");
    await view.click("Confirm end");
    assert.equal(calls, 1);
  } finally { api.mutateGoal = mutate; await view.close(); }
});

test("a failed resume acknowledgement is shown in the dialog", async () => {
  reset();
  runtimeState.conversations.s1.goal = snapshot(1, "paused");
  const mutate = api.mutateGoal;
  const run = api.runFunction;
  api.mutateGoal = async () => { throw new Error("Execution could not start"); };
  api.runFunction = async () => { throw new Error("Resume must not invoke a function"); };
  const view = await mount();
  try {
    await view.open();
    await view.click("Resume");
    assert.match(view.host.querySelector('[role="alert"]').textContent, /Execution could not start/);
  } finally { api.mutateGoal = mutate; api.runFunction = run; await view.close(); }
});

test("completion dismisses a pending end confirmation without changing the result", async () => {
  reset();
  const view = await mount();
  try {
    await view.open();
    await view.click("End");
    await frame(snapshot(2, "achieved"));
    assert.doesNotMatch(view.host.textContent, /Confirm end/);
    assert.equal(runtimeState.conversations.s1.goal.status, "achieved");
  } finally { await view.close(); }
});


test("successful pause updates the visible Goal without a websocket and survives remount", async () => {
  reset();
  const original = api.mutateGoal;
  api.mutateGoal = async () => ({ goal: snapshot(2, "paused") });
  let view = await mount();
  try {
    await view.open();
    await view.click("Pause");
    assert.match(view.host.textContent, /Paused/);
    assert.equal(runtimeState.conversations.s1.goal.status, "paused");
    await view.close();
    view = await mount();
    assert.match(view.host.textContent, /Paused/);
  } finally { api.mutateGoal = original; await view.close(); }
});

test("newer websocket snapshot persists and a late HTTP reply cannot overwrite it", async () => {
  reset();
  const original = api.mutateGoal;
  api.mutateGoal = async () => {
    window.dispatchEvent(new CustomEvent("op:ws-message", {
      detail: { type: "goal_update", data: { session_id: "s1", goal: snapshot(4, "waiting_user") } },
    }));
    return { goal: snapshot(2, "paused") };
  };
  const view = await mount();
  try {
    await view.open();
    await view.click("Pause");
    assert.match(view.host.textContent, /Waiting for you/);
    assert.equal(runtimeState.conversations.s1.goal.version, 4);
    await frame(snapshot(3, "active"));
    assert.match(view.host.textContent, /Waiting for you/);
    assert.equal(runtimeState.conversations.s1.goal.version, 4);
  } finally { api.mutateGoal = original; await view.close(); }
});

test("background Goal updates are cached for a later session switch", async () => {
  reset();
  const view = await mount();
  try {
    await frame({ ...snapshot(7, "waiting_user"), text: "Other review" }, "s2");
    assert.equal(runtimeState.conversations.s2?.goal.version, 7);
    await act(async () => useSessionStore.setState({ currentSessionId: "s2" }));
    assert.match(view.host.textContent, /Waiting for you/);
  } finally { await view.close(); }
});

test("late session hydration preserves the newest Goal while loading other session fields", async () => {
  reset();
  const { loadSessionData } = await import("../../lib/runtime-bridge/session/conversations.ts");
  const view = await mount();
  try {
    await frame(snapshot(5, "paused"));
    await act(async () => loadSessionData({ id: "s1", title: "Loaded title", messages: [], goal: snapshot(2) }));
    assert.equal(runtimeState.conversations.s1.title, "Loaded title");
    assert.equal(runtimeState.conversations.s1.goal.version, 5);
    assert.match(view.host.textContent, /Paused/);
    await act(async () => loadSessionData({ id: "s1", messages: [], goal: null }));
    assert.equal(runtimeState.conversations.s1.goal.version, 5);
    await act(async () => loadSessionData({ id: "s1", messages: [], goal: snapshot(6, "waiting_user") }));
    assert.equal(runtimeState.conversations.s1.goal.version, 6);
    assert.match(view.host.textContent, /Waiting for you/);
  } finally { await view.close(); }
});

test("terminal Goal leaves the composer without deleting its persisted result", async () => {
  for (const status of ["achieved", "cancelled", "impossible", "cleared"]) {
    reset();
    const view = await mount();
    try {
      await frame(snapshot(2, status));
      assert.equal(Boolean(view.host.querySelector("button")), false, status);
      assert.equal(runtimeState.conversations.s1.goal.status, status);
      await view.close();
      const reloaded = await mount();
      try { assert.equal(Boolean(reloaded.host.querySelector("button")), false, status); }
      finally { await reloaded.close(); }
    } finally { if (view.host.isConnected) await view.close(); }
  }
});

test("recoverable Goal states keep a usable details entry", async () => {
  for (const status of ["paused", "paused_recoverable", "waiting_user", "waiting_external", "failed", "stalled", "budget_exhausted"]) {
    reset();
    runtimeState.conversations.s1.goal = snapshot(2, status);
    const view = await mount();
    try {
      await view.open();
      assert.match(view.host.textContent, /Goal details/);
      assert.equal(view.host.querySelector("textarea").value, "Write the review");
    } finally { await view.close(); }
  }
});

test("LLM tree rows show and copy replies from legacy and current projections", async () => {
  const { TreeStep } = await import("../../components/chat/messages/execution/execution-strip.tsx");
  const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  let copied;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    clipboard: { writeText: async value => { copied = value; } },
  } });
  try {
    for (const fields of [{ raw_reply: "实际回复" }, { output: "实际回复", raw_reply: "实际回复" }]) {
      const host = document.createElement("div");
      document.body.appendChild(host);
      const root = createRoot(host);
      try {
        await act(async () => root.render(createElement(TreeStep, {
          node: { name: "LLM", node_type: "exec", params: { _content: "输入" }, ...fields },
        })));
        assert.match(host.textContent, /实际回复/);
        await act(async () => host.querySelector("button").click());
        assert.equal(copied, "实际回复");
      } finally { await act(async () => root.unmount()); host.remove(); }
    }
  } finally {
    if (oldNavigator) Object.defineProperty(globalThis, "navigator", oldNavigator);
    else delete globalThis.navigator;
  }
});

test("nested LLM content keeps Markdown, parallel refs, continuation and recursion in order", async () => {
  const { LlmNodeContent } = await import("../../components/chat/messages/execution/llm-node-content.tsx");
  const node = {
    path: "llm-root",
    name: "LLM",
    node_type: "exec",
    status: "completed",
    stream_attempts: [
      {
        attempt_id: "attempt-0",
        attempt_index: 0,
        reason: "initial",
        status: "failed",
        blocks: [{
          block_id: "old-text", message_id: "m0", block_index: 0,
          kind: "text", content: "Old partial", status: "finished",
        }],
      },
      {
        attempt_id: "attempt-1",
        attempt_index: 1,
        reason: "structured_output_repair",
        status: "completed",
        blocks: [
          {
            block_id: "before", message_id: "m1", block_index: 0,
            kind: "text", content: "Before **tools**", status: "finished",
          },
          {
            block_id: "tool-a", message_id: "m1", block_index: 1,
            kind: "tool_ref", tool_call_id: "call-a", ref_node_id: "tool-a",
            tool_name: "search", group_id: "parallel-1", status: "finished",
          },
          {
            block_id: "tool-b", message_id: "m1", block_index: 2,
            kind: "tool_ref", tool_call_id: "call-b", ref_node_id: "tool-b",
            tool_name: "lookup", group_id: "parallel-1", status: "finished",
          },
          {
            block_id: "missing", message_id: "m1", block_index: 3,
            kind: "tool_ref", tool_call_id: "call-missing", ref_node_id: "gone",
            tool_name: "gone_tool", status: "finished",
          },
          {
            block_id: "after", message_id: "m1", block_index: 4,
            kind: "text", content: "After tools", status: "finished",
          },
        ],
      },
    ],
    children: [
      {
        path: "tool-a", name: "search", status: "completed",
        params: { q: "nested" }, output: "search result",
        children: [{
          path: "inner-llm", name: "LLM", node_type: "exec", status: "completed",
          stream_attempts: [{
            attempt_id: "inner", attempt_index: 0, status: "completed",
            blocks: [{
              block_id: "inner-text", message_id: "mi", block_index: 0,
              kind: "text", content: "Inner Markdown", status: "finished",
            }],
          }],
        }],
      },
      { path: "tool-b", name: "lookup", status: "completed", output: "lookup result" },
    ],
  };
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(LlmNodeContent, {
      nodeId: "llm-root", treeRoot: node,
    })));
    const content = host.textContent;
    assert.match(content, /Before tools/);
    assert.match(content, /Parallel calls · 2/);
    assert.match(content, /search/);
    assert.match(content, /lookup/);
    assert.match(content, /unresolved/);
    assert.match(content, /Earlier attempt/);
    assert.ok(content.indexOf("Before") < content.indexOf("search"));
    assert.ok(content.indexOf("search") < content.indexOf("After tools"));
    const toolToggle = host.querySelector(".llm-nc-tool-toggle");
    assert.ok(toolToggle, "tool row should be expandable");
    await act(async () => toolToggle.click());
    assert.match(host.textContent, /Inner Markdown/);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test("legacy LLM projections retain child tools and explicit unsupported blocks", async () => {
  const { LlmNodeContent } = await import("../../components/chat/messages/execution/llm-node-content.tsx");
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(LlmNodeContent, {
      nodeId: "legacy-llm",
      treeRoot: {
        path: "legacy-llm",
        name: "LLM",
        node_type: "exec",
        output: "Legacy **Markdown**",
        stream_blocks: [
          {
            block_id: "legacy-tool",
            kind: "tool_ref",
            tool_call_id: "legacy-call",
            ref_node_id: "legacy-tool-node",
            tool_name: "read_file",
            status: "finished",
          },
          {
            block_id: "legacy-unknown",
            kind: "future_block",
            content: "preserve this marker",
            status: "finished",
          },
        ],
        children: [{
          path: "legacy-tool-node",
          name: "read_file",
          status: "cancelled",
          error: "cancelled by owner",
        }],
      },
      fallbackText: "Legacy **Markdown**",
    })));
    assert.match(host.textContent, /read_file/);
    assert.match(host.textContent, /cancelled/);
    assert.match(host.textContent, /Unsupported content block/);
    assert.match(host.textContent, /future_block/);
    assert.match(host.textContent, /Legacy Markdown/);
    await act(async () => root.render(createElement(LlmNodeContent, {
      nodeId: "archived-llm",
      treeRoot: {
        path: "archived-llm",
        name: "LLM",
        node_type: "exec",
        children: [{
          path: "archived-tool-node",
          name: "read_file",
          status: "completed",
          output: "archived result",
        }],
      },
      fallbackText: "Archived reply",
    })));
    assert.match(host.textContent, /Recovered execution nodes/);
    assert.match(host.textContent, /read_file/);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test("an empty LLM reply is labelled without inventing an output", async () => {
  const { TreeStep } = await import("../../components/chat/messages/execution/execution-strip.tsx");
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(TreeStep, {
      node: { name: "LLM", node_type: "exec", output: "", params: {} },
    })));
    assert.match(host.textContent, /No text output/);
    assert.doesNotMatch(host.textContent, /_content/);
  } finally { await act(async () => root.unmount()); host.remove(); }
});

test("function form removes advanced execution fields without replacement controls", async () => {
  const { FunctionForm } = await import('../../components/chat/composer/modes/fn-form/fn-form.tsx');
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(FunctionForm, {
      fn: { name: 'goal', params_detail: [
        { name: 'prompt', type: 'str', required: true },
        { name: 'model', type: 'str', hidden: true, advanced: true },
        { name: 'effort', type: 'str', advanced: true },
      ] }, values: {}, setValue() {}, errorParam: null, onClose() {}, onSubmit() {},
    })));
    assert.equal(host.querySelectorAll('textarea, input, select').length, 1);
    assert.equal(host.querySelector('details, summary'), null);
    assert.doesNotMatch(host.textContent, /Advanced|effort|model/);
  } finally { await act(async () => root.unmount()); host.remove(); }
});

 test("completed Goal is a compact status row with its own duration", async () => {
  reset();
  const goal={...snapshot(3,"achieved"),usage:{active_elapsed_s:582,active_time_known:true}};
  const view=await mount(createElement(GoalDetails,{sessionId:"s1",goal,historical:true}));
  try {
    assert.equal(view.host.querySelectorAll(".attach-card").length,0);
    assert.match(view.host.textContent,/Goal achieved in 9m 42s/);
    await view.open();
    assert.ok(view.host.querySelector("textarea").hasAttribute("readonly"));
  } finally {await view.close();}
});
