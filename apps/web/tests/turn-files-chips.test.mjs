import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import test, { after } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";
import { parseHTML } from "linkedom";

const webRoot = new URL("../", import.meta.url);
const webPath = dirname(fileURLToPath(new URL("package.json", webRoot)));
const bundleDir = await mkdtemp(join(webPath, ".turn-files-test-"));
after(() => rm(bundleDir, { recursive: true, force: true }));
const bundlePath = join(bundleDir, "turn-files-chips.mjs");
await build({
  absWorkingDir: webPath,
  stdin: {
    contents: [
      'export { TurnFilesChips } from "./components/chat/messages/turn-files-chips.tsx";',
      'export { setSocket } from "./lib/runtime-bridge/state.ts";',
    ].join("\n"),
    resolveDir: webPath,
    sourcefile: "turn-files-chips-entry.ts",
  },
  bundle: true,
  format: "esm",
  jsx: "automatic",
  outfile: bundlePath,
  packages: "external",
  platform: "node",
  tsconfig: join(webPath, "tsconfig.json"),
});

const parsed = parseHTML(
  '<!doctype html><html><body><div id="root"></div></body></html>',
);
globalThis.window = parsed.window;
globalThis.document = parsed.document;
globalThis.Event = parsed.window.Event;
globalThis.CustomEvent = parsed.window.CustomEvent;
globalThis.localStorage = {
  getItem(key) { return key === "agentic_locale" ? "en" : null; },
  setItem() {},
  removeItem() {},
};
globalThis.WebSocket = { OPEN: 1 };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
});
Object.defineProperty(window, "location", {
  value: { pathname: "/chat" },
  configurable: true,
});

globalThis.IntersectionObserver = class {
  constructor(callback) { this.callback = callback; }
  observe() {
    queueMicrotask(() => this.callback([{ isIntersecting: true }]));
  }
  disconnect() {}
};

class FakeSocket {
  readyState = WebSocket.OPEN;
  sent = [];
  listeners = new Map();

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  send(payload) {
    this.sent.push(JSON.parse(payload));
  }

  emit(frame) {
    const event = { data: JSON.stringify(frame) };
    for (const listener of [...(this.listeners.get("message") ?? [])]) listener(event);
  }
}

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { TurnFilesChips, setSocket } = await import(pathToFileURL(bundlePath));

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function latestReviewRequest(socket) {
  return socket.sent.filter((frame) => frame.action === "review_scope").at(-1);
}

function respond(socket, request, data) {
  socket.emit({
    type: "review_scope_result",
    data: {
      action: "review_scope",
      request_id: request.request_id,
      session_id: request.session_id,
      assistant_msg_id: request.assistant_msg_id,
      scope: "turn",
      ...data,
    },
  });
}

test("legacy file cards hide empty results, retry errors, and ignore stale responses", async () => {
  const socket = new FakeSocket();
  setSocket(socket);
  const host = document.querySelector("#root");
  const root = createRoot(host);
  const props = (id) => ({
    key: id,
    assistantMsgId: id,
    sessionIdOverride: "session-1",
    blocks: [{ type: "tool", tool: "apply_patch", is_error: false }],
  });

  await act(async () => { root.render(createElement(TurnFilesChips, props("empty"))); });
  await flush();
  const emptyRequest = latestReviewRequest(socket);
  assert.ok(emptyRequest);
  await act(async () => { respond(socket, emptyRequest, { files: [], file_count: 0 }); });
  assert.equal(host.querySelector(".turn-files-card"), null);
  assert.equal(host.querySelector(".turn-files-review"), null);

  await act(async () => { root.render(createElement(TurnFilesChips, props("error"))); });
  await flush();
  const errorRequest = latestReviewRequest(socket);
  await act(async () => {
    socket.emit({
      type: "operation_error",
      data: {
        action: "review_scope",
        request_id: errorRequest.request_id,
        code: "temporary",
        message: "temporary failure",
      },
    });
  });
  assert.match(host.textContent, /Could not load file changes/);
  assert.equal(host.querySelector(".turn-files-review"), null);
  const beforeRetry = socket.sent.filter((frame) => frame.action === "review_scope").length;
  await act(async () => {
    host.querySelector(".turn-files-load-error button").dispatchEvent(
      new window.Event("click", { bubbles: true }),
    );
  });
  await flush();
  assert.equal(
    socket.sent.filter((frame) => frame.action === "review_scope").length,
    beforeRetry + 1,
  );

  await act(async () => { root.render(createElement(TurnFilesChips, props("old"))); });
  await flush();
  const oldRequest = latestReviewRequest(socket);
  await act(async () => { root.render(createElement(TurnFilesChips, props("current"))); });
  await flush();
  const currentRequest = latestReviewRequest(socket);
  await act(async () => {
    respond(socket, oldRequest, { error: "late failure" });
  });
  assert.equal(host.querySelector(".turn-files-load-error"), null);
  await act(async () => { respond(socket, currentRequest, { files: [], file_count: 0 }); });
  assert.equal(host.textContent, "");

  await act(async () => { root.unmount(); });
  setSocket(null);
});
