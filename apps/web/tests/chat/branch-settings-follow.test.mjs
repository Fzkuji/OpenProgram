import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../../${specifier.slice(2)}`, import.meta.url).href;
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

const values = new Map();
globalThis.window = {
  addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
  location: { pathname: "/chat", hash: "", search: "" },
  history: { state: null, replaceState() {}, pushState() {} },
};
globalThis.document = {
  addEventListener() {}, removeEventListener() {}, getElementById() { return null; },
  querySelector() { return null; }, querySelectorAll() { return []; },
  createElement() { return { style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {} }; },
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.WebSocket = { OPEN: 1 };

const { useSessionStore } = await import("../../lib/session-store/index.ts");
const { sentControls } = await import("../../components/chat/composer/submit/send-chat-message.ts");
const { settingOrigin } = await import("../../lib/session-store/branch-settings.ts");
const runs = await import("../../lib/chat/run/other-branch-runs.ts");

const controls = { thinking: "high", webSearchEnabled: false };

test("a message in an existing conversation sends no settings; the branch decides", () => {
  useSessionStore.setState({ conversations: { s1: { id: "s1" } } });
  assert.deepEqual(sentControls("s1", { draftTouched: ["thinking", "webSearch"] }, controls), {});
});

test("a new conversation sends only the controls the person changed", () => {
  useSessionStore.setState({ conversations: {} });
  assert.deepEqual(sentControls(null, undefined, controls), {});
  assert.deepEqual(sentControls("draft", { draftTouched: ["webSearch"] }, controls), { web_search: false });
  assert.deepEqual(sentControls(null, { draftTouched: ["thinking", "webSearch"] }, controls),
    { thinking_effort: "high", web_search: false });
});

test("a setting's origin says whether it follows the Agent and what it would follow", () => {
  const resolved = { values: { web_search: true }, sources: { web_search: "agent", thinking_effort: "branch" },
    inherited: { web_search: true, thinking_effort: "medium" } };
  assert.deepEqual(settingOrigin(resolved, "web_search"), { follows: true, inherited: true });
  assert.deepEqual(settingOrigin(resolved, "thinking_effort"), { follows: false, inherited: "medium" });
  assert.equal(settingOrigin(undefined, "web_search"), null);
});

test("runs on other branches come and go with their frames and never list the shown branch", () => {
  let state = {};
  state = runs.applyBranchFrame(state, "branch_running_task",
    { session_id: "s", branch_id: "b", branch_name: "Night", wait_state: "input", msg_id: "m1" });
  state = runs.applyBranchFrame(state, "branch_running_task", { session_id: "s", branch_id: "a", msg_id: "m0" });
  assert.deepEqual(runs.runsBeside(state, "s", "a").map((r) => [r.branchId, r.name, r.state]),
    [["b", "Night", "input"]]);
  assert.equal(runs.badgeTone("input"), "warning");
  assert.equal(runs.badgeTone("running"), "neutral");
  // A clear that names another run leaves this one.
  state = runs.applyBranchFrame(state, "branch_running_task_clear", { session_id: "s", branch_id: "b", msg_id: "old" });
  assert.equal(runs.runsBeside(state, "s", "a").length, 1);
  state = runs.applyBranchFrame(state, "branch_running_task_clear", { session_id: "s", branch_id: "b", msg_id: "m1" });
  assert.equal(runs.runsBeside(state, "s", "a").length, 0);
});

test("a load seeds the runs already going, leaving out the selected branch's", () => {
  runs.useOtherBranchRuns.getState().seed("s", [
    { branch_id: "b", status: "running", selected: false },
    { branch_id: "a", status: "running", selected: true },
    { branch_id: "c", status: "failed" },
  ]);
  const seeded = runs.useOtherBranchRuns.getState().runs.s;
  assert.deepEqual(Object.keys(seeded).sort(), ["b", "c"]);
  assert.equal(seeded.c.state, "failed");
});
