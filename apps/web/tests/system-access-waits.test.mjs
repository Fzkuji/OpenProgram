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
window.location = { hostname: "localhost", pathname: "/chat", hash: "", search: "" };
window.history = { replaceState() {}, pushState() {} };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
document.visibilityState = "visible";
const timers = new Set();
window.setInterval = callback => { timers.add(callback); return callback; };
window.clearInterval = callback => timers.delete(callback);
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { SystemAccessWaits } = await import("../components/chat/messages/system-access-waits.tsx");
const { rememberSystemAccessWait } = await import("../lib/system-access-wait-state.ts");
const waiting = {wait_id:"os-wait",session_id:"session",execution_id:"execution",required_capabilities:["screen_recording"]};

 test("durable waiting UI opens native setup once, restores without prompting, and never retries", async () => {
  let waits = [], granted = false, failRead = false;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push([url, options.method || "GET"]);
    if (url.includes("/waits?")) return failRead ? new Response(null,{status:503}) : Response.json({waits});
    const row = {id:"screen_recording",status:granted ? "granted" : "not_granted",can_request:true};
    return Response.json(options.method === "POST" ? row : {capabilities:[row]});
  };
  const host = document.createElement("div"); document.body.append(host);
  let root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SystemAccessWaits,{sessionId:"session"})));
    assert.equal(host.textContent, "");
    waits = [waiting];
    await act(async () => window.dispatchEvent(new CustomEvent("op:system-access", {detail:{type:"system_access.waiting",data:{...waiting,live:true}}})));
    assert.match(host.textContent,/Waiting for system authorization/);
    assert.equal(calls.filter(([,method])=>method==="POST").length,1);
    const second = {...waiting, wait_id:"second-wait",execution_id:"second-execution"};
    waits = [waiting, second];
    await act(async () => window.dispatchEvent(new CustomEvent("op:system-access", {detail:{type:"system_access.waiting",data:second}})));
    await act(async () => window.dispatchEvent(new Event("focus")));
    assert.equal(host.querySelectorAll('[role="status"]').length,1);
    assert.equal(calls.filter(([,method])=>method==="POST").length,1);
    await act(async () => root.unmount());
    assert.equal(timers.size,0);
    root = createRoot(host);
    await act(async () => root.render(createElement(SystemAccessWaits,{sessionId:"session"})));
    assert.match(host.textContent,/Waiting for system authorization/);
    assert.equal(calls.filter(([,method])=>method==="POST").length,1);
    failRead = true;
    await act(async () => window.dispatchEvent(new Event("focus")));
    assert.match(host.textContent,/Waiting for system authorization/);
    failRead = false;
    granted = true; waits = [];
    await act(async () => window.dispatchEvent(new Event("focus")));
    assert.equal(host.textContent, "");
    assert.equal(calls.every(([url])=>url.startsWith("/api/system/access")),true);
    assert.equal(calls.filter(([,method])=>method==="POST").length,1);
 } finally { await act(async () => root.unmount()); host.remove(); }
 });

test("a live wait received before the session pane mounts is adopted once", async () => {
  const sid = "pre-mount-session";
  const wait = {...waiting, session_id: sid, wait_id: "pre-mount-wait"};
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push([url, options.method || "GET"]);
    if (url.includes("/waits?")) return Response.json({waits: [wait]});
    const row = {id: "screen_recording", status: "not_granted", can_request: true};
    return Response.json(options.method === "POST" ? row : {capabilities: [row]});
  };
  rememberSystemAccessWait(wait, true);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SystemAccessWaits, {sessionId: sid})));
    assert.match(host.textContent, /Waiting for system authorization/);
    assert.equal(calls.filter(([, method]) => method === "POST").length, 1);
    await act(async () => root.unmount());
    assert.equal(calls.filter(([, method]) => method === "POST").length, 1);
  } finally { host.remove(); }
});

test("replayed waits restore status without opening native setup", async () => {
  const sid = "replayed-session";
  const wait = {...waiting, session_id: sid, wait_id: "replayed-wait"};
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push([url, options.method || "GET"]);
    if (url.includes("/waits?")) return Response.json({waits: [wait]});
    const row = {id: "screen_recording", status: "not_granted", can_request: true};
    return Response.json(options.method === "POST" ? row : {capabilities: [row]});
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SystemAccessWaits, {sessionId: sid})));
    assert.match(host.textContent, /Waiting for system authorization/);
    assert.equal(calls.filter(([, method]) => method === "POST").length, 0);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
