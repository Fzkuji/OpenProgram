import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "react" && context.parentURL?.endsWith("use-pending-run-function.ts")) {
      return { shortCircuit: true, url: "data:text/javascript,export const useEffect = (effect) => globalThis.launchEffects.push(effect);" };
    }
    if (specifier.endsWith("owner-auth-bootstrap.ts")) {
      return { shortCircuit: true, url: "data:text/javascript,export const waitForOwnerAuthBootstrap = async () => {};" };
    }
    if (specifier === "@/lib/session-store") {
      return { shortCircuit: true, url: "data:text/javascript,export const useSessionStore = {getState: () => globalThis.launcherSession};" };
    }
    if (specifier === "@/lib/i18n") {
      return { shortCircuit: true, url: "data:text/javascript,export const translateText = (...variants) => variants[0];" };
    }
    if (specifier.startsWith("@/")) {
      return { shortCircuit: true, url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      return { shortCircuit: true, url: existsSync(fileURLToPath(`${base}.ts`)) ? `${base}.ts` : `${base}/index.ts` };
    }
    return nextResolve(specifier, context);
  },
});

const notices = [];
globalThis.window = { location: { pathname: "/chat" }, dispatchEvent: (event) => notices.push(event.detail) };
globalThis.launcherSession = { activeChatKey: "chat-a", openFnForm: (fn) => opened.push(fn.name) };
const opened = [];
const { useFunctions } = await import("../lib/state/functions-store.ts");
const { runtimeState } = await import("../lib/runtime-bridge/state.ts");
const actions = await import("../lib/state/functions-actions.ts");

function reset() {
  notices.length = 0;
  opened.length = 0;
  launcherSession.activeChatKey = "chat-a";
  useFunctions.getState().setFunctions([]);
  runtimeState.availableFunctions = [];
}

test("Use resolves an uncached workflow and publishes it for favorites", async () => {
  reset();
  const workflow = { name: "weekly_report", params: { task: { type: "string" } } };
  globalThis.fetch = async () => Response.json([workflow]);
  assert.equal(typeof actions.openFunctionForm, "function");
  await actions.openFunctionForm("weekly_report");
  assert.deepEqual(opened, ["weekly_report"]);
  assert.deepEqual(useFunctions.getState().functions, [workflow]);
  assert.deepEqual(runtimeState.availableFunctions, [workflow]);
});

test("a failed refresh preserves the last usable catalog", async () => {
  reset();
  const existing = [{ name: "existing" }];
  useFunctions.getState().setFunctions(existing);
  runtimeState.availableFunctions = existing;
  globalThis.fetch = async () => Response.json({ error: "unavailable" }, { status: 503 });
  await actions.refreshFunctionsList();
  assert.deepEqual(useFunctions.getState().functions, existing);
  assert.deepEqual(runtimeState.availableFunctions, existing);
});

test("an unavailable workflow produces a visible error", async () => {
  reset();
  globalThis.fetch = async () => Response.json([]);
  await actions.openFunctionForm("weekly_report");
  assert.deepEqual(opened, []);
  assert.equal(notices.at(-1).tone, "error");
  assert.match(notices.at(-1).message, /weekly_report/);
});

test("leaving the chat during resolution does not open its form elsewhere", async () => {
  reset();
  let reply;
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  globalThis.fetch = () => new Promise((resolve) => { reply = resolve; started(); });
  const pending = actions.openFunctionForm("weekly_report");
  await ready;
  launcherSession.activeChatKey = "chat-b";
  reply(Response.json([{ name: "weekly_report" }]));
  await pending;
  assert.deepEqual(opened, []);
});

test("a newer launch supersedes an earlier in-flight request", async () => {
  reset();
  let reply;
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  globalThis.fetch = () => new Promise((resolve) => { reply = resolve; started(); });
  const pending = actions.openFunctionForm("weekly_report");
  await ready;
  useFunctions.getState().setFunctions([{ name: "other" }]);
  await actions.openFunctionForm("other");
  reply(Response.json([{ name: "weekly_report" }]));
  await pending;
  assert.deepEqual(opened, ["other"]);
});

test("an aborted launch neither opens a form nor publishes its response", async () => {
  reset();
  let reply;
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  globalThis.fetch = () => new Promise((resolve) => { reply = resolve; started(); });
  const controller = new AbortController();
  const pending = actions.openFunctionForm("weekly_report", controller.signal);
  await ready;
  controller.abort();
  reply(Response.json([{ name: "weekly_report" }]));
  await pending;
  assert.deepEqual(opened, []);
  assert.deepEqual(useFunctions.getState().functions, []);
  assert.deepEqual(notices, []);
});


test("a discarded Strict Mode setup does not consume the pending sidebar launch", async (t) => {
  reset();
  globalThis.launchEffects = [];
  window.location.search = "";
  useFunctions.getState().setFunctions([{ name: "weekly_report" }]);
  const launcher = await import("../lib/use-pending-run-function.ts");
  t.mock.timers.enable({ apis: ["setTimeout"] });
  launcher.setPendingRunFunction({ name: "weekly_report" });
  launcher.usePendingRunFunction("/chat");
  const cleanupDiscarded = launchEffects.pop()();
  cleanupDiscarded();
  launcher.usePendingRunFunction("/chat");
  const cleanup = launchEffects.pop()();
  t.mock.timers.tick(0);
  await Promise.resolve();
  assert.deepEqual(opened, ["weekly_report"]);
  assert.equal(launcher.takePendingRunFunction(), null);
  cleanup();
});

test("an older refresh cannot replace a newer successful catalog", async () => {
  reset();
  const replies = [];
  const starts = [];
  globalThis.fetch = () => new Promise((resolve) => { replies.push(resolve); starts.shift()?.(); });
  const olderStarted = new Promise((resolve) => starts.push(resolve));
  const older = actions.refreshFunctionsList();
  await olderStarted;
  const newerStarted = new Promise((resolve) => starts.push(resolve));
  const newer = actions.refreshFunctionsList();
  await newerStarted;
  const current = [{ name: "weekly_report" }];
  replies[1](Response.json(current));
  await newer;
  replies[0](Response.json([]));
  await older;
  assert.deepEqual(useFunctions.getState().functions, current);
  assert.deepEqual(runtimeState.availableFunctions, current);
});

test("a shared store update supersedes an in-flight HTTP snapshot", async () => {
  reset();
  let reply;
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  globalThis.fetch = () => new Promise((resolve) => { reply = resolve; started(); });
  const pending = actions.refreshFunctionsList();
  await ready;
  const current = [{ name: "weekly_report" }];
  useFunctions.getState().setFunctions(current);
  reply(Response.json([]));
  await pending;
  assert.deepEqual(useFunctions.getState().functions, current);
  assert.deepEqual(runtimeState.availableFunctions, current);
});
