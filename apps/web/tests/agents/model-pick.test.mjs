import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

// A model pick names the branch the pane shows, like every branch setting,
// and a refused pick moves the pane to the branch shown instead of a toast.
const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const real = (path) => new URL(path, import.meta.url).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    const map = {
      "@/lib/net/api": real("../../lib/net/api.ts"),
      "@/lib/agents/branch-target": real("../../lib/agents/branch-target.ts"),
      "@/lib/session-store/branch-settings": real("../../lib/session-store/branch-settings.ts"),
      "./permission-state": real("../../lib/session-store/permission-state.ts"),
    };
    if (map[specifier]) return { url: map[specifier], shortCircuit: true };
    if (specifier === "./fetch-client" || specifier === "@/lib/net/fetch-client") {
      return { url: stub(`export const jsonFetch = (url, init) => globalThis.__pick.fetch(url, init);`), shortCircuit: true };
    }
    if (specifier === "@/lib/session-store") {
      return { url: stub(`export const useSessionStore = { getState: () => globalThis.__pick.store };`), shortCircuit: true };
    }
    if (specifier === "@/lib/format-utils/toast") {
      return { url: stub(`export const showToast = (text) => globalThis.__pick.toasts.push(text);`), shortCircuit: true };
    }
    if (specifier === "@/lib/runtime-bridge/providers") {
      return { url: stub(`export const loadSessionAgentSettings = async (sid) => { globalThis.__pick.reads.push(sid); };`), shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const state = globalThis.__pick = {
  requests: [], replies: [], toasts: [], reads: [],
  store: null,
  fetch(url, init) {
    state.requests.push([url, JSON.parse(init.body)]);
    const reply = state.replies.shift();
    return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
  },
};

function fresh(branch) {
  state.requests.length = 0; state.replies.length = 0; state.toasts.length = 0; state.reads.length = 0;
  state.store = {
    currentSessionId: "s", activeChatKey: null,
    conversations: { s: { id: "s" } },
    composerSettingsBySession: { s: { active_branch_id: branch, thinking: "high" } },
    agentSettingsBySession: { s: { chat: { provider: "p", model: "old" } } },
    agentSettings: { chat: { provider: "p", model: "old" } },
    setComposerSettings(patch, sid) { this.composerSettingsBySession[sid] = { ...this.composerSettingsBySession[sid], ...patch }; },
    setSessionAgentSettings(sid, settings) { this.agentSettingsBySession[sid] = settings; },
    setAgentSettings(settings) { this.agentSettings = { ...this.agentSettings, ...settings }; },
  };
}

function conflict(code, activeBranchId) {
  const error = new Error("409");
  error.status = 409;
  error.body = JSON.stringify({ error: "branch_config_conflict", code, active_branch_id: activeBranchId });
  return error;
}

const { pickBranchModel } = await import("../../lib/agents/model-pick.ts");

test("the pick names the branch the pane shows", async () => {
  fresh("a");
  state.replies.push({ provider: "q", model: "picked", branch_id: "a", active_branch_id: "b",
    settings: {}, chat: { provider: "q", model: "picked", thinking: { options: [{ value: "low" }] }, fast: true } });
  await pickBranchModel("s", "q", "picked");
  assert.deepEqual(state.requests, [["/api/model", { provider: "q", model: "picked", session_id: "s", branch_id: "a" }]]);
  const settings = state.store.composerSettingsBySession.s;
  assert.equal(settings.active_branch_id, "a", "the branch selected elsewhere does not move the pane");
  assert.equal(state.store.agentSettingsBySession.s.chat.model, "picked", "the pane shows its branch's model");
  assert.equal(state.store.agentSettingsBySession.s.chat.fast, true, "with that model's controls");
  assert.equal(state.store.agentSettings.chat.model, "picked", "the focused slot follows its pane");
  assert.deepEqual(state.reads, [], "the selected branch's settings are not read over it");
});

test("a conversation with no branch recorded guards the first one", async () => {
  fresh(null);
  state.replies.push({ provider: "q", model: "m", branch_id: "a", active_branch_id: "a", settings: {}, chat: {} });
  await pickBranchModel("s", "q", "m");
  assert.deepEqual(state.requests[0][1], { provider: "q", model: "m", session_id: "s", expected_branch_id: null });
  assert.equal(state.store.composerSettingsBySession.s.active_branch_id, "a", "the pane adopts the branch created");
  assert.deepEqual(state.reads, ["s"]);
});

test("a branch that is gone: the pick lands on the branch shown, with that branch's settings", async () => {
  fresh("a");
  state.replies.push(conflict("branch_gone", "b"), {
    provider: "q", model: "picked", branch_id: "b", active_branch_id: "b",
    settings: { thinking_effort: "max", web_search: false, service_tier: "priority", sandbox_enabled: true },
    chat: { provider: "q", model: "picked" },
  });
  await pickBranchModel("s", "q", "picked");
  assert.deepEqual(state.requests.map(([, body]) => body.branch_id), ["a", "b"]);
  const settings = state.store.composerSettingsBySession.s;
  assert.equal(settings.active_branch_id, "b");
  assert.equal(settings.thinking, "max", "the branch's own stored effort");
  assert.equal(settings.webSearch, false);
  assert.equal(settings.fast, true);
  assert.equal(state.store.agentSettingsBySession.s.chat.model, "picked", "and the user's pick");
});

test("another error still fails the pick", async () => {
  fresh("a");
  const error = new Error("500"); error.status = 500; error.body = "{}";
  state.replies.push(error);
  await assert.rejects(pickBranchModel("s", "q", "picked"));
  assert.equal(state.requests.length, 1);
  assert.equal(state.store.agentSettingsBySession.s.chat.model, "old");
});
