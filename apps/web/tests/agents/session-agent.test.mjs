import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

const stub = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/net/fetch-client") {
      return { url: stub(`export const jsonFetch = (url, init) => { globalThis.__sessionAgent.requests.push([url, init?.method, init?.body]); return Promise.resolve({ session_id: "c1", agent_id: "watcher", model: { provider: "anthropic", id: "opus" }, ...globalThis.__sessionAgent.reply }); };`), shortCircuit: true };
    }
    if (specifier === "@/lib/session-store") {
      return { url: stub(`export const useSessionStore = { getState: () => globalThis.__sessionAgent.store };`), shortCircuit: true };
    }
    if (specifier === "@/lib/runtime-bridge/state") {
      return { url: stub(`export const runtimeState = globalThis.__sessionAgent.runtime;`), shortCircuit: true };
    }
    if (specifier === "@/lib/runtime-bridge/providers") {
      return { url: stub(`export const loadAgentSettings = async () => { globalThis.__sessionAgent.reloaded += 1; }; export const loadSessionAgentSettings = async (sid) => { globalThis.__sessionAgent.paneReads.push(sid); };`), shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const upserts = [];
globalThis.__sessionAgent = {
  requests: [],
  reloaded: 0,
  paneReads: [],
  reply: {},
  runtime: { conversations: { c1: { id: "c1", agent_id: "main" } } },
  store: {
    currentSessionId: "c1",
    composerSettingsBySession: {},
    setComposerSettings: (patch, sid) => {
      const store = globalThis.__sessionAgent.store;
      store.composerSettingsBySession[sid] = { ...store.composerSettingsBySession[sid], ...patch };
    },
    conversations: { c1: { id: "c1", title: "Docs", agent_id: "main" } },
    upsertConversation: (summary) => upserts.push(summary),
  },
};

const { switchSessionAgent } = await import("../../lib/agents/session-agent.ts");

test("an existing conversation switches Agent on the server and takes its model", async () => {
  await switchSessionAgent("c1", "watcher");
  const state = globalThis.__sessionAgent;
  assert.deepEqual(state.requests, [["/api/sessions/c1/agent", "POST", JSON.stringify({ agent_id: "watcher", expected_branch_id: null })]]);
  assert.deepEqual(upserts, [{ id: "c1", title: "Docs", agent_id: "watcher" }]);
  assert.equal(state.runtime.conversations.c1.agent_id, "watcher");
  assert.equal(state.reloaded, 1, "the model chip reloads the Agent's model");
  assert.deepEqual(state.paneReads, ["c1"], "the pane's own entry is read again too");
});

test("a pane that is not focused switches its own branch's Agent and reads only its own model", async () => {
  const state = globalThis.__sessionAgent;
  state.requests.length = 0; state.paneReads.length = 0; state.reloaded = 0; upserts.length = 0;
  state.store.currentSessionId = "other";
  state.store.composerSettingsBySession.c1 = { active_branch_id: "branch_a" };
  state.runtime.conversations.c1.agent_id = "main";
  state.reply = { branch_id: "branch_a", active_branch_id: "branch_b" };
  await switchSessionAgent("c1", "watcher");
  assert.deepEqual(state.requests, [["/api/sessions/c1/agent", "POST", JSON.stringify({ agent_id: "watcher", branch_id: "branch_a" })]]);
  assert.deepEqual(state.paneReads, ["c1"]);
  assert.equal(state.reloaded, 0, "the focused conversation's settings are not read for another pane");
  assert.deepEqual(upserts, [], "the sidebar keeps the selected branch's Agent");
  assert.equal(state.runtime.conversations.c1.agent_id, "main");
  assert.equal(state.store.composerSettingsBySession.c1.active_branch_id, "branch_a");
});
