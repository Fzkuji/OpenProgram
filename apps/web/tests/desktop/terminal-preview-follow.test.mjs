import assert from "node:assert/strict";
import test from "node:test";

const listeners = new Map();
globalThis.window = {
  addEventListener: (type, handler) => listeners.set(type, handler),
  dispatchEvent() {},
};
let emit = () => {};
window.openprogramTerminals = {
  windowId: "main",
  resource: async () => ({ ok: true, items: [] }),
  agentTicket: async () => ({ ok: false }),
  onResource: (callback) => { emit = callback; return () => {}; },
};

const { installTerminalResourceBridge } = await import("../../lib/desktop/terminal-resources.ts");
const { appPreviewTarget, previewResource, resetBrowserResources, useBrowserResourceStore } =
  await import("../../lib/chat/resources/session-resources.ts");

const terminal = (patch = {}) => ({
  terminal_id: "t1", generation: "g1", preset: "shell", status: "running", start_cwd: "/tmp",
  pid: 1, exit_code: null, output_end: 0, shared: false, session_ids: ["conv"],
  input_revision: 0, human_input_pending: false, in_use: false, ...patch,
});

test("the chat preview follows a terminal when the Agent takes it or types into it", () => {
  resetBrowserResources();
  useBrowserResourceStore.setState({ viewedBranch: { conv: "br" } });
  installTerminalResourceBridge();

  emit(terminal());
  assert.equal(appPreviewTarget("conv", "br"), null, "an idle terminal is not followed");

  emit(terminal({ in_use: true }));
  assert.equal(appPreviewTarget("conv", "br"), "terminal:t1");

  previewResource("conv", "br", "assoc-page");
  emit(terminal({ in_use: true }));
  assert.equal(appPreviewTarget("conv", "br"), null, "no new Agent input, no switch back");

  emit(terminal({ in_use: true, input_revision: 1 }));
  assert.equal(appPreviewTarget("conv", "br"), "terminal:t1");

  emit(terminal({ in_use: true, input_revision: 2, status: "exited" }));
  emit(terminal({ terminal_id: "t2", in_use: true, session_ids: ["other"] }));
  assert.equal(appPreviewTarget("conv", "br"), "terminal:t1", "other conversations' terminals stay out");
});
