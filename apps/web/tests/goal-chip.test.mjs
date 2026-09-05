import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";
import ts from "typescript";

const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) return {
      url: "data:text/javascript,export default {}", shortCircuit: true,
    };
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href : null;
    if (base) for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
      if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    // Only presentation wrappers are simplified; GoalChip, hooks, API and
    // shared session cache are the production modules under test.
    if (url.endsWith("/components/ui/dialog.tsx")) return {
      format: "module", shortCircuit: true,
      source: `export const Dialog = ({open, children}) => open ? children : null;
        export const DialogContent = ({children}) => children;
        export const DialogDescription = DialogContent;
        export const DialogFooter = DialogContent;
        export const DialogHeader = DialogContent;
        export const DialogTitle = DialogContent;`,
    };
    if (url.endsWith(".tsx")) return {
      format: "module", shortCircuit: true,
      source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
        compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      }).outputText,
    };
    return nextLoad(url, context);
  },
});

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
document.oninput = null; // Enable React's native input-event path in this DOM.
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
// Use the real i18n module with an explicit browser preference. The old loader
// stub targeted a removed i18n.ts path and let the host locale affect assertions.
globalThis.localStorage = { getItem(key) { return key === "agentic_locale" ? "en" : null; }, setItem() {}, removeItem() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.location = { pathname: "/chat", hash: "" };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { GoalChip } = await import("../components/chat/goal-chip.tsx");
const { runtimeState, setSocket } = await import("../lib/runtime-bridge/state.ts");
const { updateStatus } = await import("../lib/runtime-bridge/ui.ts");
const { useSessionStore } = await import("../lib/session-store/index.ts");
const { api } = await import("../lib/net/api.ts");

const snapshot = (version, status = "active") => ({
  goal_id: "goal-1", version, revision: 1, text: "Write the review", status,
});
async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => root.render(createElement(GoalChip)));
  return {
    host,
    async click(text) {
      const button = [...host.querySelectorAll("button")].find(item => item.textContent === text);
      assert.ok(button, `missing button ${text}: ${host.textContent}`);
      await act(async () => button.dispatchEvent(new Event("click", { bubbles: true })));
    },
    async open() { await act(async () => host.querySelector("button").click()); },
    async close() { await act(async () => root.unmount()); host.remove(); },
  };
}
async function frame(goal, sid = "s1") {
  await act(async () => window.dispatchEvent(new CustomEvent("op:ws-message", {
    detail: { type: "goal_update", data: { session_id: sid, goal } },
  })));
}
function reset() {
  runtimeState.conversations = { s1: { id: "s1", goal: snapshot(1) } };
  useSessionStore.setState({ currentSessionId: "s1" });
}

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
    return { goal, execution: { execution_id: "exec-resume", status: "cancelled", finished: true } };
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

test("progress does not overwrite an unsaved budget and a conflict keeps the draft", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1), budget: { max_turns: 5 } };
  const view = await mount();
  try {
    await view.open();
    const input = view.host.querySelector('[aria-label="Turns"]');
    await typeInto(input, "12");
    await frame({ ...snapshot(2), budget: { max_turns: 5 } });
    assert.equal(input.value, "12");
    await frame({ ...snapshot(3), budget: { max_turns: 9 } });
    assert.equal(input.value, "12");
    assert.match(view.host.textContent, /changed elsewhere/i);
    const save = [...view.host.querySelectorAll("button")].find(node => node.textContent === "Save limits");
    assert.equal(save.disabled, true);
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

test("answering does not resume past unsaved edits", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "waiting_user"), questions: [
    { id: "scope", prompt: "Scope?", status: "pending" },
  ] };
  const mutate = api.mutateGoal;
  const run = api.runFunction;
  let runs = 0;
  api.mutateGoal = async () => ({ goal: snapshot(2, "paused"), invoke: { name: "goal", kwargs: { resume: true } } });
  api.runFunction = async () => { runs++; return {}; };
  const view = await mount();
  try {
    await view.open();
    await typeInto(view.host.querySelector("textarea"), "Unsaved goal");
    await typeInto(view.host.querySelector('[aria-label="Answer: Scope?"]'), "Editing");
    await view.click("Submit answer");
    assert.equal(runs, 0);
    assert.equal(view.host.querySelector("textarea").value, "Unsaved goal");
    assert.match(view.host.textContent, /Save or discard/);
  } finally { api.mutateGoal = mutate; api.runFunction = run; await view.close(); }
});

test("a conflict fetches the latest Goal without replacing the local draft", async () => {
  reset();
  const { HttpError } = await import("../lib/net/fetch-client.ts");
  const mutate = api.mutateGoal;
  const get = api.getGoal;
  api.mutateGoal = async () => { throw new HttpError("Goal changed", 409); };
  api.getGoal = async () => ({ goal: { ...snapshot(2), revision: 2, text: "Saved remotely" } });
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

test("a saved answer reports an unfinished previous execution without starting work", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "waiting_user"), questions: [
    { id: "scope", prompt: "Scope?", status: "pending" },
  ] };
  const mutate = api.mutateGoal;
  const run = api.runFunction;
  let runs = 0;
  api.mutateGoal = async () => ({ goal: snapshot(2, "paused"), resume_error: "Previous Goal execution is cancelling" });
  api.runFunction = async () => { runs++; return {}; };
  const view = await mount();
  try {
    await view.open();
    await typeInto(view.host.querySelector('[aria-label="Answer: Scope?"]'), "Editing");
    await view.click("Answer and resume");
    assert.equal(runs, 0);
    assert.match(view.host.textContent, /Answer saved.*Previous Goal execution is cancelling/);
    assert.equal(runtimeState.conversations.s1.goal.status, "paused");
  } finally { api.mutateGoal = mutate; api.runFunction = run; await view.close(); }
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
  api.mutateGoal = async () => ({ goal: snapshot(1, "paused"), invoke: { name: "goal", kwargs: {} } });
  api.runFunction = async () => ({ error: "Execution could not start" });
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

test("paused Goal role settings are editable and saved with snapshot preconditions", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(1, "paused"), roles: {
    work: { provider: "worker", model: "writer", effort: "high", timeout_s: 30 },
    judge: { provider: "judge", model: "reviewer", effort: "low", timeout_s: 40 },
  } };
  const mutate = api.mutateGoal;
  let sent;
  api.mutateGoal = async (_sid, body) => {
    sent = body;
    return { goal: { ...snapshot(2, "paused"), role_requests: {
      model: "worker:writer", effort: "high", timeout_s: 30,
      judge_model: "judge:new-reviewer", judge_effort: "low", judge_timeout_s: 40,
    } } };
  };
  const view = await mount();
  try {
    await view.open();
    const model = view.host.querySelector('[aria-label="Judge model"]');
    assert.ok(model);
    await typeInto(model, "new-reviewer");
    await view.click("Save roles");
    assert.equal(sent.action, "roles");
    assert.equal(sent.roles.judge.model, "new-reviewer");
    assert.equal(sent.expected.goal_id, "goal-1");
    assert.match(view.host.textContent, /validated on resume/);
  } finally { api.mutateGoal = mutate; await view.close(); }
});

test("saved work and judge identities remain visible after remount", async () => {
  reset();
  runtimeState.conversations.s1.goal = { ...snapshot(2, "paused"), roles: {
    work: { provider: "worker", model: "writer", model_provider: "worker", effort: "high", timeout_s: 17 },
    judge: { provider: "judge", model: "reviewer", model_provider: "judge", effort: "low", timeout_s: 23 },
  }};
  for (let i = 0; i < 2; i++) {
    const view = await mount();
    try {
      await view.open();
      assert.match(view.host.textContent, /worker\/writer/);
      assert.match(view.host.textContent, /judge\/reviewer/);
      assert.match(view.host.textContent, /high · 17s/);
      assert.match(view.host.textContent, /low · 23s/);
    } finally { await view.close(); }
  }
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
      detail: { type: "goal_update", data: { session_id: "s1", goal: snapshot(4, "paused_recoverable") } },
    }));
    return { goal: snapshot(2, "paused") };
  };
  const view = await mount();
  try {
    await view.open();
    await view.click("Pause");
    assert.match(view.host.textContent, /Paused after restart/);
    assert.equal(runtimeState.conversations.s1.goal.version, 4);
    await frame(snapshot(3, "active"));
    assert.match(view.host.textContent, /Paused after restart/);
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
  const { loadSessionData } = await import("../lib/runtime-bridge/conversations.ts");
  const view = await mount();
  try {
    await frame(snapshot(5, "paused"));
    await act(async () => loadSessionData({ id: "s1", title: "Loaded title", messages: [], goal: snapshot(2) }));
    assert.equal(runtimeState.conversations.s1.title, "Loaded title");
    assert.equal(runtimeState.conversations.s1.goal.version, 5);
    assert.match(view.host.textContent, /Paused/);
    await act(async () => loadSessionData({ id: "s1", messages: [], goal: null }));
    assert.equal(runtimeState.conversations.s1.goal.version, 5);
    await act(async () => loadSessionData({ id: "s1", messages: [], goal: snapshot(6, "paused_recoverable") }));
    assert.equal(runtimeState.conversations.s1.goal.version, 6);
    assert.match(view.host.textContent, /Paused after restart/);
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
  const { TreeStep } = await import("../components/chat/messages/execution-strip.tsx");
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
        assert.equal(JSON.parse(copied).output, "实际回复");
      } finally { await act(async () => root.unmount()); host.remove(); }
    }
  } finally {
    if (oldNavigator) Object.defineProperty(globalThis, "navigator", oldNavigator);
    else delete globalThis.navigator;
  }
});

test("an empty LLM reply is labelled without inventing an output", async () => {
  const { TreeStep } = await import("../components/chat/messages/execution-strip.tsx");
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
