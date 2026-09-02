import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const WEB_ROOT = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

class FakeSocket {
  static OPEN = 1;

  readyState = FakeSocket.OPEN;
  sent = [];
  listeners = new Map();

  addEventListener(type, listener) {
    const current = this.listeners.get(type) ?? new Set();
    current.add(listener);
    this.listeners.set(type, current);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  send(raw) {
    this.sent.push(JSON.parse(raw));
  }

  emit(type, data) {
    for (const listener of this.listeners.get(type) ?? []) listener({ data });
  }
}

globalThis.WebSocket = FakeSocket;
const { setSocket } = await import("../lib/runtime-bridge/state.ts");
const { filesWsRequest, fileResponseMatchesOwner } = await import(
  "../lib/state/files-shared.ts"
);

test("stale cursor response is accepted after unrelated same-type frame", async () => {
  const socket = new FakeSocket();
  setSocket(socket);
  const response = filesWsRequest(
    "project_file_search",
    { project_id: "project-a", query: "needle", cursor: "expired", snapshot_id: "snap-a" },
    "project_file_search_result",
  );
  const [{ request_id: requestId }] = socket.sent;

  socket.emit("message", JSON.stringify({
    type: "project_file_search_result",
    data: {
      request_id: "00000000-0000-4000-8000-000000000001",
      action: "project_file_search",
      project_id: "project-b",
      status: "ready",
      snapshot_id: "snap-b",
    },
  }));
  socket.emit("message", JSON.stringify({
    type: "project_file_search_result",
    data: {
      request_id: requestId,
      action: "project_file_search",
      project_id: "project-a",
      query: "needle",
      status: "stale",
      error_code: "STALE_SNAPSHOT",
      snapshot_id: null,
      error: "search cursor expired",
    },
  }));

  assert.deepEqual(await response, {
    request_id: requestId,
    action: "project_file_search",
    project_id: "project-a",
    query: "needle",
    status: "stale",
    error_code: "STALE_SNAPSHOT",
    snapshot_id: null,
    error: "search cursor expired",
  });
});

test("owner mismatch remains rejected for stale responses", () => {
  assert.equal(fileResponseMatchesOwner(
    { project_id: "project-b", status: "stale", snapshot_id: null },
    { project_id: "project-a", snapshot_id: "snap-a" },
  ), false);
  assert.equal(fileResponseMatchesOwner(
    { project_id: "project-a", status: "stale", snapshot_id: null },
    { project_id: "project-a", snapshot_id: "snap-a" },
  ), true);
  assert.equal(fileResponseMatchesOwner(
    { project_id: "project-a", status: "stale", snapshot_id: "snap-other" },
    { project_id: "project-a", snapshot_id: "snap-a" },
  ), false);
});
