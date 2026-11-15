import assert from "node:assert/strict";
import { test } from "node:test";

import { installLoader } from "../_support/load.mjs";

installLoader({
  stubs: { "@/lib/net/ws/ws-events": "" },
  replace: { "/lib/desktop/terminal-resources.ts": "export const installTerminalResourceBridge = () => {};" },
});

const { desktopTerminalId, destroyStaleTerminals } = await import("@/lib/desktop/bridge/bridge-terminals");

function bridge() {
  const stopped = [];
  return { stopped, windowId: "w7", terminal: { stop: (id) => stopped.push(id) } };
}

test("a terminal id is scoped to the window and the preset", () => {
  assert.equal(desktopTerminalId({ windowId: "w7" }, "shell"), "terminal:w7:shell");
  assert.equal(desktopTerminalId({ windowId: "w7" }, "claude"), "terminal:w7:claude");
});

test("a preset whose built-in tab is open keeps its process", () => {
  const b = bridge();
  destroyStaleTerminals(b, [
    { kind: "builtin", page: "terminal" },
    { kind: "builtin", page: "claude" },
  ]);
  assert.deepEqual(b.stopped, []);
});

test("a preset whose built-in tab was closed has its process stopped", () => {
  const b = bridge();
  destroyStaleTerminals(b, [{ kind: "builtin", page: "terminal" }, { kind: "session", page: "claude" }]);
  assert.deepEqual(b.stopped, ["terminal:w7:claude"]);
  const all = bridge();
  destroyStaleTerminals(all, []);
  assert.deepEqual(all.stopped, ["terminal:w7:shell", "terminal:w7:claude"]);
});

test("a shell without terminal support is left alone", () => {
  assert.doesNotThrow(() => destroyStaleTerminals({ windowId: "w7" }, []));
});
