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
    if (url.endsWith("/lib/i18n.ts")) return {
      format: "module", shortCircuit: true,
      source: 'export const useTranslation = () => ({locale:"en", text:(en)=>en}); export const translateText = (en)=>en;',
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
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.location = { pathname: "/chat", hash: "" };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { GoalChip } = await import("../components/chat/goal-chip.tsx");
const { runtimeState } = await import("../lib/runtime-bridge/state.ts");
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
