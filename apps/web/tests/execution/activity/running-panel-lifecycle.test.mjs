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
    if (specifier.endsWith(".module.css")) {
      return { url: "data:text/javascript,export default {}", shortCircuit: true };
    }
    if (specifier === "@/lib/agents/pending-inbox") {
      // The cross-conversation inbox reads through React Query; nothing waits here.
      return { url: "data:text/javascript,export const usePendingInbox = () => ({ items: [], loaded: true }); export const pendingInOrder = (items) => items; export const openPendingItem = () => {};", shortCircuit: true };
    }
    if (specifier === "@/components/agents/agent-avatar") {
      return { url: "data:text/javascript,export const AgentAvatarById = () => null; export const useConversationAgentShape = () => 'cloud'", shortCircuit: true };
    }
    // Pages this file never opens.
    const page = { "./program-page": "ProgramPage", "./command-page": "CommandPage", "./scheduled-task-page": "ScheduledTaskPage", "./open-work-page": "OpenWorkPage", "./task-runs-page": "TaskRunsPage" }[specifier];
    if (page) return { url: `data:text/javascript,export const ${page} = () => null`, shortCircuit: true };
    if (specifier === "./always-on-row") return { url: "data:text/javascript,export const AlwaysOnRow = () => null; export const ReportInside = () => null; export const reportForRun = () => false; export const reportNameForRun = () => null", shortCircuit: true };
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
let respond;
globalThis.fetch = (...args) => respond(...args);
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { RunningPanel } = await import("../../../components/right-sidebar/running/running-panel.tsx");
const { MessageList } = await import("../../../components/chat/messages/list/message-list.tsx");
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");

function response(payload, status = 200) {
  return { ok: status === 200, status, async json() { return payload; }, async text() { return JSON.stringify(payload); } };
}
async function mount(Component, props, check) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => { root.render(createElement(Component, props)); });
    await check(host, root);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
}
test("activity never overlaps polls for the same resource and aborts on unmount", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const requests = [];
  respond = async (url, init) => {
    requests.push({ url: String(url), signal: init.signal });
    return new Promise(() => {});
  };
  await mount(RunningPanel, { sessionId: "session-one", active: true }, async (host) => {
    await act(async () => {
      t.mock.timers.tick(3100);
      window.dispatchEvent(new Event("online"));
      window.dispatchEvent(new Event("online"));
      // An execution event invalidates the pending resource without
      // overlapping reads or requiring a manual refresh control.
      window.dispatchEvent(new Event("op:execution-update"));
      assert.equal(host.querySelector('button[aria-label="Refresh activity"]'), null);
    });
    // Executions, programs, the conversation's own work (always-on, open
    // work, scheduled tasks) and the agents' roster: one read each.
    assert.equal(requests.length, 4);
    assert.ok(requests.some(request => request.url.endsWith("/api/session/session-one/activity/roster")));
    assert.ok(requests.some(request => request.url.endsWith("/api/sessions/session-one/work")));
    assert.equal(new Set(requests.map(request => request.url)).size, requests.length);
  });
  assert.ok(requests.every(request => request.signal.aborted));
});

test("runs that cannot be read stay quiet a moment, then say so under Agents", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  respond = async () => response({ error: "denied" }, 403);
  await mount(RunningPanel, { sessionId: "session-one", active: true }, async (host) => {
    // Nothing read yet: the main agent is listed, with no loading text.
    assert.match(host.textContent, /Main agent/);
    assert.doesNotMatch(host.textContent, /Could not read|unavailable|Retrying|Loading/);
    await act(async () => { t.mock.timers.tick(5000); });
    assert.match(host.textContent, /Could not read this conversation's runs/);
    assert.doesNotMatch(host.textContent, /Retrying|Updating|saved records/);
  });
});

test("a conversation's last read draws at once, and a fresh one is not read again", async () => {
  const { useExecutionDebugger } = await import("../../../lib/execution/use-execution-debugger.ts");
  const { forgetActivityReads } = await import("../../../lib/execution/activity-reads.ts");
  forgetActivityReads();
  useSessionStore.setState({ wsStatus: "open" });
  const Probe = () => {
    const state = useExecutionDebugger(true, "session-read", undefined, false);
    return createElement("p", null, `runs:${state.executions.length} read:${state.fetchedAt ? "yes" : "no"}`);
  };
  const requests = [];
  const snapshot = { execution_id: "run", session_id: "session-read", status: "completed", updated_at: 1, event_sequence: 0 };
  respond = async (url) => {
    requests.push(String(url));
    return response({ items: [{ kind: "execution", id: "run", execution_id: "run", session_id: "session-read", status: "completed", started_at: 1, snapshot }], branches: [], now: 2 });
  };
  await mount(Probe, {}, async (host) => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(host.textContent, "runs:1 read:yes");
  });
  assert.equal(requests.length, 1);
  // Coming back: the last read is there in the first frame, and nothing
  // moved since, so it is not read again.
  respond = async (url) => { requests.push(String(url)); return new Promise(() => {}); };
  await mount(Probe, {}, async (host) => {
    assert.equal(host.textContent, "runs:1 read:yes");
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(requests.length, 1);
    // A run moved: the list is read again, with the old rows still drawn.
    await act(async () => { window.dispatchEvent(new Event("op:execution-update")); });
    assert.equal(requests.length, 2);
    assert.equal(host.textContent, "runs:1 read:yes");
  });
  // A move while away makes the kept read stale: entry reads again.
  await mount(Probe, {}, async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    assert.equal(requests.length, 3);
  });
  useSessionStore.setState({ wsStatus: "connecting" });
});

test("conversation parent ignores historical update fixtures", async () => {
  const calls = [];
  respond = async (...args) => { calls.push(args); throw new Error("unexpected update request"); };
  const previousSessionId = useSessionStore.getState().currentSessionId;
  useSessionStore.setState({ currentSessionId: "session-one" });
  try {
    // The transcript asks the Agents list whether this conversation is always-on.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const WithQueries = () => createElement(QueryClientProvider, { client }, createElement(MessageList));
    await mount(WithQueries, {}, async (host) => {
      window.dispatchEvent(new CustomEvent("op:self-update-test-object", {
        detail: { phase: "pending", session_id: "session-one", update_id: "update-one" },
      }));
      window.dispatchEvent(new CustomEvent("op:self-update-reopen", {
        detail: { status: "pending", sessionId: "session-one", updateId: "update-one" },
      }));
      await act(async () => {});
      assert.equal(calls.filter(([url]) => String(url).includes("/api/self-updates")).length, 0);
      assert.equal(host.querySelector('[data-update-id]'), null);
      assert.equal(host.querySelector('[aria-label="Self-update conversation recovery"]'), null);
      assert.doesNotMatch(host.textContent, /Self-update|Update status|Waiting for the original conversation/);
    });
  } finally {
    useSessionStore.setState({ currentSessionId: previousSessionId });
  }
});


test("activity list does not load unselected execution events or debugger bodies", async () => {
  const urls = [];
  respond = async (url) => {
    urls.push(String(url));
    if (String(url).endsWith("/executions")) return response({ items: [{ snapshot: {
      execution_id: "exec-one", session_id: "session-one", status: "completed", updated_at: 1,
      event_sequence: 100, status_version: 1,
    } }] });
    if (String(url).includes("/processes")) return response({ items: [] });
    throw new Error("Unselected detail should not be requested");
  };
  await mount(RunningPanel, { sessionId: "session-one", active: true }, async () => {
    assert.ok(urls.some(url => url.endsWith("/executions")));
    assert.equal(urls.filter(url => /\/(events|debugger)[?]/.test(url)).length, 0);
    assert.equal(urls.filter(url => url.includes("/activity/runs")).length, 0);
  });
});
