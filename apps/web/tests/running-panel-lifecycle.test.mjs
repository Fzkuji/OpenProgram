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
let respond;
globalThis.fetch = (...args) => respond(...args);
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { RunningPanel } = await import("../components/right-sidebar/running-panel.tsx");
const { MessageList } = await import("../components/chat/messages/message-list.tsx");
const { useSessionStore } = await import("../lib/session-store/index.ts");

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
    assert.equal(requests.length, 2);
    assert.equal(new Set(requests.map(request => request.url)).size, requests.length);
  });
  assert.ok(requests.every(request => request.signal.aborted));
});

test("initial failures add no phantom update history and preserve activity errors", async () => {
  respond = async () => response({ error: "denied" }, 403);
  await mount(RunningPanel, { sessionId: "session-one", active: true }, async (host) => {
    assert.match(host.textContent, /Could not load activity/);
    assert.doesNotMatch(host.textContent, /Nothing is running|Loading/);
  });
});

test("conversation parent ignores historical update fixtures", async () => {
  const calls = [];
  respond = async (...args) => { calls.push(args); throw new Error("unexpected update request"); };
  const previousSessionId = useSessionStore.getState().currentSessionId;
  useSessionStore.setState({ currentSessionId: "session-one" });
  try {
    await mount(MessageList, {}, async (host) => {
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
