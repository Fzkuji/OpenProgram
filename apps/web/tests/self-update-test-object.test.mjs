import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { EventEmitter } from "node:events";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { registerUiVerificationIpc } = require("../../desktop/self-update-ui.js");
const { createUiVerificationGuard } = require("../../desktop/self-update-ui-guard.js");
const nonce = "a".repeat(64);
const operation = { kind: "test_object", object_id: "rename-fixture", action: "rename",
  initial_title: "Before verification", title: "Approved rename", cleanup: "restore-and-remove" };

// Execute both real React components and the real native isolated-world program.
// Only React scheduling/DOM/Electron primitives and transport are fixtures.
async function exercise({ contract, request, send, png = "fixture-png", interrupt = false }) {
  const window = new EventTarget();
  window.openprogramDesktop = { windowId: "main" };
  const location = { pathname: `/s/${contract.session_id}` };
  const area = { isConnected: true, scrollTop: 500, scrollLeft: 0, clientHeight: 600,
    getBoundingClientRect: () => ({ width: 800 }) };
  let statusNode = null, dialogNode = null, inputProps, saveProps, cancelProps;
  class Input {
    get value() { return this.text; }
    set value(value) { this.text = value; }
    dispatchEvent(event) { if (event.type === "input") inputProps.onChange({ target: this }); return true; }
  }
  const input = new Input();
  const save = { disabled: false, click: () => saveProps.onClick() };
  const cancel = { disabled: false, click: () => cancelProps.onClick() };
  const dialog = { querySelector: (selector) => selector === "input" ? input : selector.includes('"save"') ? save : cancel,
    querySelectorAll: () => [input] };
  const document = { body: { style: { pointerEvents: "", overflow: "" }, getAttribute: () => null },
    getElementById: (id) => id === "chatArea" ? area : id === "selfUpdateTestObjectState" ? statusNode
      : id === "selfUpdateTestObjectDialog" ? dialogNode : null,
    querySelector: () => dialogNode,
    querySelectorAll: () => dialogNode ? [dialogNode] : [] };
  const socket = { readyState: 1, send(raw) {
    void Promise.resolve(send(JSON.parse(raw))).then((data) => window.dispatchEvent(new CustomEvent("op:ws-message", {
      detail: { type: "self_update_test_object", data },
    })));
  } };
  let slots = [], cursor = 0, mounted = false, cleanup, Component;
  const react = {
    useRef: (value) => { const i = cursor++; return slots[i] ??= { current: value }; },
    useState: (value) => { const i = cursor++; if (!(i in slots)) slots[i] = value;
      return [slots[i], (next) => { slots[i] = next; render(); }]; },
    useEffect: (fn) => { if (!mounted) queueMicrotask(() => { cleanup = fn(); }); },
  };
  const jsx = (type, props) => typeof type === "function" ? type(props) : { type, props };
  const context = { window, document, location, WebSocket: { OPEN: 1 }, setTimeout, clearTimeout, Date, console };
  function load(relative, extras = {}) {
    const exports = {};
    const code = ts.transpileModule(readFileSync(new URL(relative, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(code, { ...context, exports, require: (name) => {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "fragment" };
      if (extras[name]) return extras[name];
      if (name === "@/lib/runtime-bridge/state") return { getSocket: () => socket };
      if (name === "@/lib/i18n") return { useTranslation: () => ({ t: (s) => s, text: (s) => s }) };
      if (name.endsWith("/dialog")) return Object.fromEntries(["Dialog", "DialogContent", "DialogFooter", "DialogHeader", "DialogTitle"].map(s => [s, s]));
      if (name.endsWith("/input")) return { Input: "input" };
      if (name.endsWith("/button")) return { Button: "button" };
      throw new Error(`unexpected import ${name}`);
    } });
    return exports;
  }
  const rename = load("../components/chat/rename-dialog.tsx");
  Component = load("../components/self-update-test-object.tsx", { "./chat/rename-dialog": rename }).SelfUpdateTestObject;
  function render() {
    cursor = 0;
    statusNode = dialogNode = null;
    const tree = Component();
    function visit(node) {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) { node.forEach(visit); return; }
      const props = node.props ?? {};
      if (props.id === "selfUpdateTestObjectState") statusNode = { getAttribute: (key) => props[key] };
      if (props.id === "selfUpdateTestObjectDialog") dialogNode = dialog;
      if (node.type === "input") { inputProps = props; input.value = props.value; }
      if (props["data-rename-action"] === "save") saveProps = props;
      if (props["data-rename-action"] === "cancel") cancelProps = props;
      visit(props.children);
    }
    visit(tree);
    mounted = true;
  }
  render();
  await new Promise(resolve => setImmediate(resolve));
  const isolated = vm.createContext({ ...context, HTMLInputElement: Input, Event, CustomEvent,
    requestAnimationFrame: (fn) => setImmediate(fn) });
  const contents = Object.assign(new EventEmitter(), { id: 7, isDestroyed: () => false,
    getURL: () => `http://127.0.0.1:18100${location.pathname}`, getOSProcessId: () => 1234,
    mainFrame: { url: `http://127.0.0.1:18100${location.pathname}` },
    session: { webRequest: { onBeforeRequest() {}, onBeforeSendHeaders() {} } },
    executeJavaScriptInIsolatedWorld: async (_world, scripts) => vm.runInContext(scripts[0].code, isolated) });
  let attached = false;
  contents.debugger = { isAttached: () => attached, attach: () => { attached = true; }, detach: () => { attached = false; },
    sendCommand: async (method) => method === "Target.getTargetInfo" ? { targetInfo: { targetId: "fixture-target" } }
      : { nodes: [{ nodeId: "1", role: { value: "dialog" } }] } };
  contents.capturePage = async () => {
    assert.equal(statusNode.getAttribute("data-phase"), "renamed");
    assert.equal(input.value, contract.interaction.title);
    if (interrupt) { contents.emit("before-input-event", {}); throw new Error("interrupted"); }
    return { isEmpty: () => false, getSize: () => ({ width: 16, height: 16 }), toPNG: () => Buffer.from(png, "base64") };
  };
  const win = Object.assign(new EventEmitter(), { id: 1, webContents: contents, isDestroyed: () => false,
    isVisible: () => true, isMinimized: () => false, getBounds: () => ({ x: 0, y: 0, width: 800, height: 600 }) });
  const handlers = new Map();
  const ipc = Object.assign(new EventEmitter(), { handle: (key, fn) => handlers.set(key, fn) });
  const guard = createUiVerificationGuard(ipc);
  registerUiVerificationIpc({ ipcMain: guard.ipcMain, windows: new Map([["main", { win, visibleViewIds: new Set() }]]),
    origin: "http://127.0.0.1:18100", guard, request, app: {},
    installation: () => ({ app_path: "/Applications/OpenProgram.app", candidate_sha: contract.candidate_sha, app_pid: 456 }) });
  try {
    const result = await handlers.get("self-update:ui-capture")({ sender: contents, senderFrame: contents.mainFrame }, contract.nonce);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(dialogNode, null);
    assert.equal(statusNode, null);
    assert.equal(attached, false);
    return result;
  } finally { cleanup?.(); }
}

if (process.argv.includes("--backend")) {
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  const cfg = JSON.parse(raw);
  const call = async (url, body, signal) => {
    const response = await fetch(url, { method: body === null ? "GET" : "POST", signal,
      headers: { Authorization: `Bearer ${cfg.token}`, "Content-Type": "application/json" },
      body: body === null ? undefined : JSON.stringify(body), redirect: "error" });
    if (!response.ok) throw new Error(`fixture HTTP ${response.status}`);
    return response.json();
  };
  const contract = cfg.contract;
  const result = await exercise({ contract, png: cfg.png, interrupt: cfg.interrupt,
    request: (_nonce, body, signal) => call(cfg.url, body, signal),
    send: (body) => call(cfg.command_url, body) });
  process.stdout.write(JSON.stringify(result));
} else {
  for (const interrupt of [false, true]) test(`actual rename control and native cleanup (interrupt=${interrupt})`, async () => {
    const contract = { schema: 1, nonce, update_id: "su_test", attempt: 1, session_id: "p1", candidate_sha: "b".repeat(40),
      worker_pid: 123, check_id: "rename", deadline: Date.now() / 1000 + 5, max_output_bytes: 1048576, action: "capture", interaction: operation };
    const commands = [];
    let receipt;
    const result = await exercise({ contract, interrupt,
      request: async (_nonce, body) => { if (!body) return contract; receipt = body; return { ok: true, nonce }; },
      send: async (cmd) => { commands.push(cmd); return { ok: true, nonce, object_id: operation.object_id,
        title: cmd.title, phase: cmd.op === "rename" ? "renamed" : "restored" }; } });
    assert.equal(result.ok, !interrupt);
    assert.equal(commands[0].title, operation.title);
    if (interrupt) assert.equal(receipt, undefined);
    else { assert.equal(commands[1].title, operation.initial_title); assert.equal(receipt.interaction.restored, operation.initial_title); }
  });
}
