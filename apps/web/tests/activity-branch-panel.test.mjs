import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/use-execution-debugger") return { url: "data:text/javascript,export const useExecutionDebugger = () => globalThis.activityController", shortCircuit: true };
    if (specifier === "@/lib/use-managed-processes") return { url: "data:text/javascript,export const useManagedProcesses = () => globalThis.activityProcesses", shortCircuit: true };
    if (specifier === "./debugger-panel") return { url: "data:text/javascript,export const DebuggerPanel = () => null", shortCircuit: true };
    if (specifier === "@/lib/runtime-bridge/state") return { url: "data:text/javascript,export const getSocket = () => globalThis.approvalSocket", shortCircuit: true };
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
const { RunningPanel } = await import("../components/right-sidebar/running-panel.tsx");
const run = (id, status, started_at) => ({ execution_id: id, session_id: "session", status, started_at, updated_at: started_at, parent_execution_id: null, effect_summary: {}, task_label: id });
const branches = [{ branch_id: "session:tip", session_id: "session", head_msg_id: "tip", execution_ids: ["first", "last"] }];
async function mounted(executions, check, branchList = branches, processes = [], stateOverrides = {}, processOverrides = {}) {
  globalThis.activityController = { executions, branches: branchList, connection: { state: "connected" }, fetchedAt: 1, selectExecution() {}, ...stateOverrides };
  globalThis.activityProcesses = { items: processes, stale: false, loaded: true, ...processOverrides };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(RunningPanel, { active: true, sessionId: "session" })));
    const click = async text => {
      const button = [...host.querySelectorAll("button, [role=button]")].find(b => b.textContent.includes(text));
      assert.ok(button, text);
      await act(async () => button.click());
    };
    await check(host, click);
  } finally { await act(async () => root.unmount()); host.remove(); }
}
test("Activity counts one continuous branch and retains both executions in details", async () => {
  await mounted([run("last", "completed", 2), run("first", "failed", 1)], async (host, click) => {
    assert.match(host.textContent, /History1/);
    await click("History");
    assert.equal(host.querySelectorAll("time").length, 0, "turn timestamps belong in details");
    await click("first");
    assert.match(host.textContent, /Execution history for this conversation branch/);
    assert.match(host.textContent, /first/); assert.match(host.textContent, /last/);
  });
});
test("one branch moves to In progress instead of also appearing in History", async () => {
  await mounted([run("last", "running", 2), run("first", "completed", 1)], async host => {
    assert.match(host.textContent, /In progress1/); assert.doesNotMatch(host.textContent, /History/);
  });
});
test("actual wait on an earlier turn remains actionable in the branch", async () => {
  await mounted([run("last", "completed", 2), run("first", "paused", 1)], async host => {
    assert.match(host.textContent, /Needs attention1/); assert.doesNotMatch(host.textContent, /History/);
  });
});
test("distinct conversation branches count twice", async () => {
  await mounted([run("last", "completed", 2), run("first", "completed", 1)], async host => {
    assert.match(host.textContent, /History2/);
  }, [{...branches[0], execution_ids:["first"]}, {...branches[0], branch_id:"other", execution_ids:["last"]}]);
});
test("a program keeps its completed conversation branch in progress", async () => {
  await mounted([run("last", "completed", 2), run("first", "completed", 1)], async host => {
    assert.match(host.textContent, /In progress1/); assert.doesNotMatch(host.textContent, /History/);
  }, branches, [{ id:"program", execution_id:"first", status:"running", command:"python", started_at:1 }]);
});
test('a separately associated active child keeps its parent branch visible', async () => {
  for (const status of ['running', 'paused']) {
    const child = {...run('last', status, 2), view_parent_execution_id:'first'};
    await mounted([child, run('first', 'completed', 1)], async host => {
      assert.match(host.textContent, status === 'paused' ? /Needs attention2/ : /In progress2/);
      assert.doesNotMatch(host.textContent, /History/);
    }, [{...branches[0], execution_ids:['first']}, {...branches[0], branch_id:'child', execution_ids:['last']}]);
  }
});


test("Activity never flashes an error for loading, reconnection, or another conversation's failure", async () => {
  for (const connection of [{state:"reconnecting"}, {state:"gap"}, {state:"stale"}, {state:"stale",message:"previous failure",errorSessionId:"other"}]) {
    await mounted([], async host => {
      assert.doesNotMatch(host.textContent, /statuses are unavailable|Could not load activity/);
    }, [], [], {connection,fetchedAt:null}, {loaded:true});
  }
});
test("Activity still reports confirmed current conversation read failures", async () => {
  for (const processFailed of [false,true]) await mounted([], async host => {
    assert.match(host.textContent, /statuses are unavailable/);
  }, [], [], {connection: processFailed ? {state:"connected"} : {state:"stale",message:"request failed",errorSessionId:"session"}}, {stale:processFailed});
});
