import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/net/fetch-client") {
      return { url: stub(`export const jsonFetch = (url, init) => globalThis.__writes.fetch(url, init);`), shortCircuit: true };
    }
    if (specifier === "@/lib/session-store") {
      return { url: stub(`export const useSessionStore = { getState: () => globalThis.__writes.store };`), shortCircuit: true };
    }
    if (specifier === "@/lib/format-utils/toast") {
      return { url: stub(`export const showToast = (text) => globalThis.__writes.toasts.push(text);`), shortCircuit: true };
    }
    if (specifier === "@/lib/session-store/branch-settings") {
      return { url: new URL("../../lib/session-store/branch-settings.ts", import.meta.url).href, shortCircuit: true };
    }
    if (specifier === "./permission-state") {
      return { url: new URL("../../lib/session-store/permission-state.ts", import.meta.url).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const state = globalThis.__writes = {
  requests: [],
  toasts: [],
  replies: [],
  store: {
    conversations: { s: { id: "s" } },
    composerSettingsBySession: { s: { active_branch_id: "a", thinking: "high", fastByModel: { "p:m": true } } },
    setComposerSettings(patch, sid) {
      this.composerSettingsBySession[sid] = { ...this.composerSettingsBySession[sid], ...patch };
    },
  },
  fetch(url, init) {
    state.requests.push(JSON.parse(init.body));
    const reply = state.replies.shift();
    return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
  },
};

const { persistBranchSettings, pendingBranchSettings } = await import("../../lib/agents/branch-settings.ts");

function conflict(activeBranchId) {
  const error = new Error("branch_config_conflict");
  error.status = 409;
  error.body = JSON.stringify({ error: "branch_config_conflict", code: "branch_gone", active_branch_id: activeBranchId });
  return error;
}

test("a setting is written to the branch the pane shows", async () => {
  state.replies.push({ branch_id: "a", active_branch_id: "b", settings: {} });
  persistBranchSettings("s", { thinking: "low" });
  await pendingBranchSettings("s");
  assert.deepEqual(state.requests.at(-1), { thinking_effort: "low", branch_id: "a" });
  assert.equal(state.store.composerSettingsBySession.s.active_branch_id, "a", "the selected branch elsewhere does not move the pane");
  assert.deepEqual(state.toasts, []);
});

test("the effort default puts the branch back to inherit through reset_fields, never a null", async () => {
  state.replies.push({ branch_id: "a", active_branch_id: "b", settings: {} });
  persistBranchSettings("s", { thinking: "" });
  await pendingBranchSettings("s");
  assert.deepEqual(state.requests.at(-1), { reset_fields: ["thinking_effort"], branch_id: "a" });
});

test("a branch that is gone: the pane takes the branch shown, reads its settings and applies the change there", async () => {
  state.requests.length = 0;
  state.replies.push(conflict("b"), {
    branch_id: "b", active_branch_id: "b",
    settings: { thinking_effort: "max", web_search: false, service_tier: "", sandbox_enabled: true },
  });
  persistBranchSettings("s", { fast: true });
  await pendingBranchSettings("s");
  assert.deepEqual(state.requests, [
    { service_tier: "priority", branch_id: "a" },
    { service_tier: "priority", branch_id: "b" },
  ]);
  const settings = state.store.composerSettingsBySession.s;
  assert.equal(settings.active_branch_id, "b");
  assert.equal(settings.thinking, "max", "the branch's own stored effort");
  assert.equal(settings.webSearch, false);
  assert.equal(settings.sandbox, true);
  assert.equal(settings.fast, true, "the change the user made");
  assert.deepEqual(state.toasts, [], "no dead-end toast");
});
