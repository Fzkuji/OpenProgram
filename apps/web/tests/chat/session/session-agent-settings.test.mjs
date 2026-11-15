/**
 * Each conversation keeps its own Agent and model: a draft started from an
 * Agent keeps that Agent and its model after the first ACK and the end of
 * the turn, and split panes never share a model entry.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../../../${specifier.slice(2)}`, import.meta.url).href;
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
globalThis.window = { addEventListener: () => {}, dispatchEvent: () => {}, location: { pathname: "/chat" } };
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.requestAnimationFrame = undefined;

const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");
const { conversationAgentId } = await import("../../../lib/agents/settle-agent-invocation.ts");

const agentOf = (sid) => conversationAgentId(useSessionStore.getState(), sid, sid);

test("a draft started from an Agent keeps that Agent and model after its ACK and turn end", () => {
  const sid = "local_researcher";
  const store = useSessionStore.getState();
  store.setCurrentDraft(sid);
  // The focused default chat runs another model on the global slot.
  store.setAgentSettings({ chat: { provider: "openai", model: "gpt-main" } });
  store.setComposerSettings({
    agentInvocation: {
      agentId: "researcher", label: "Researcher",
      resolvedModel: { provider: "openai", id: "gpt-6.1-sol" },
      thinkingLevels: ["low", "high"], defaultThinking: "high",
    },
  }, sid);
  assert.equal(agentOf(sid), "researcher");

  applyChatWsMessage({ type: "chat_ack", data: { session_id: sid, msg_id: "m1" } });
  let state = useSessionStore.getState();
  assert.equal(state.composerSettingsBySession[sid]?.agentInvocation, undefined, "the ACK consumes the pending pick");
  assert.equal(agentOf(sid), "researcher", "the Agent chip does not fall back to the default Agent");
  assert.equal(state.agentSettingsBySession[sid]?.chat?.model, "gpt-6.1-sol");
  assert.deepEqual(state.agentSettingsBySession[sid]?.chat?.thinking?.options?.map((o) => o.value), ["low", "high"]);

  // A summary written before the list knows the Agent, then the turn ends.
  state.upsertConversation({ id: sid, title: "Research", pinned: false, archived: false, unread: false });
  applyChatWsMessage({ type: "chat_response", data: { type: "result", session_id: sid, msg_id: "m1", content: "done" } });
  assert.equal(agentOf(sid), "researcher");
  assert.equal(useSessionStore.getState().agentSettingsBySession[sid]?.chat?.model, "gpt-6.1-sol");

  // The list's own value wins once it names the Agent (e.g. after a switch).
  useSessionStore.getState().upsertConversation({ id: sid, title: "Research", agent_id: "writer", pinned: false, archived: false, unread: false });
  assert.equal(agentOf(sid), "writer");
});

test("split panes keep separate model entries", () => {
  const store = useSessionStore.getState();
  store.setSessionAgentSettings("left", { chat: { provider: "openai", model: "gpt-a" } });
  store.setSessionAgentSettings("right", { chat: { provider: "anthropic", model: "model-b" } });
  store.setSessionAgentSettings("right", { chat: { provider: "openai", model: "gpt-c" } });
  const state = useSessionStore.getState();
  assert.equal(state.agentSettingsBySession.left.chat.model, "gpt-a");
  assert.equal(state.agentSettingsBySession.right.chat.model, "gpt-c");
});

test("agent_settings_changed leaves every chip on its own model until its read lands", async () => {
  const { agentSettingsChanged } = await import("../../../lib/runtime-bridge/providers.ts");
  const { runtimeState } = await import("../../../lib/runtime-bridge/state.ts");
  const store = useSessionStore.getState();
  // Split view: the focused pane shows conversation B, the other one A.
  store.setCurrentConv("conv-b");
  runtimeState.currentSessionId = "conv-b";
  store.setAgentSettings({ chat: { provider: "p", model: "luna" } });
  store.setSessionAgentSettings("conv-b", { chat: { provider: "p", model: "luna" } });
  store.setSessionAgentSettings("conv-a", { chat: { provider: "p", model: "sol" } });
  const epoch = store.agentSettingsEpoch;
  const pending = [];
  const oldFetch = globalThis.fetch;
  const oldDocument = globalThis.document;
  // The token badge read after the settings read: no badge here.
  globalThis.document = { getElementById: () => null };
  globalThis.fetch = (url) => new Promise((resolve) => pending.push({ url, resolve }));
  try {
    // Another window picked astra on A's selected branch; the frame names no conversation.
    const done = agentSettingsChanged({ chat: { provider: "p", model: "astra" } });
    let state = useSessionStore.getState();
    assert.equal(state.agentSettings.chat?.model, "luna", "the focused chip does not show A's model");
    assert.equal(state.agentSettingsBySession["conv-b"]?.chat?.model, "luna");
    assert.equal(state.agentSettingsBySession["conv-a"]?.chat?.model, "sol", "the other pane keeps its chip while it rereads");
    assert.equal(state.agentSettingsEpoch, epoch + 1, "shown panes are told to read again");
    assert.match(pending[0].url, /session_id=conv-b/, "the focused conversation reads its own");
    pending[0].resolve({ json: async () => ({ active_branch_id: null, chat: { provider: "p", model: "luna" } }) });
    await done;
    state = useSessionStore.getState();
    assert.equal(state.agentSettings.chat?.model, "luna");
    assert.equal(state.agentSettingsBySession["conv-a"]?.chat?.model, "sol");
    pending.slice(1).forEach((p) => p.resolve({ json: async () => ({}) }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  } finally {
    globalThis.fetch = oldFetch;
    globalThis.document = oldDocument;
    runtimeState.currentSessionId = null;
    useSessionStore.getState().setCurrentConv(null);
  }
});

test("late model settings fetched on another branch cannot replace the selected branch", async () => {
  window.fetch = globalThis.fetch;
  window.location.hash = "";
  const { loadSessionAgentSettings } = await import("../../../lib/runtime-bridge/providers.ts");
  const store = useSessionStore.getState();
  const sid = "branched-model";
  store.setComposerSettings({active_branch_id: "a"}, sid);
  const pending = [];
  const oldFetch = globalThis.fetch;
  globalThis.fetch = () => new Promise(resolve => pending.push(resolve));
  try {
    const before = loadSessionAgentSettings(sid);
    store.setComposerSettings({active_branch_id: "b"}, sid);
    const after = loadSessionAgentSettings(sid);
    pending[1]({json: async () => ({active_branch_id: "b", chat: {provider: "p", model: "model-b"}})});
    await after;
    pending[0]({json: async () => ({active_branch_id: "a", chat: {provider: "p", model: "model-a"}})});
    await before;
    assert.equal(useSessionStore.getState().agentSettingsBySession[sid].chat.model, "model-b");
  } finally { globalThis.fetch = oldFetch; }
});

test("a pane on a branch that is not selected reads its own model back after a broadcast wipes it", async () => {
  const { loadSessionAgentSettings } = await import("../../../lib/runtime-bridge/providers.ts");
  const sid = "unselected-branch-model";
  const store = useSessionStore.getState();
  // The server's selected branch is b; this pane shows a, where X was picked.
  store.setComposerSettings({active_branch_id: "a"}, sid);
  store.setSessionAgentSettings(sid, {chat: {provider: "p", model: "X"}});
  const urls = [];
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    urls.push(url);
    const named = new URL(url, "http://x").searchParams.get("branch_id");
    // Like the server: a named branch answers with its own stored model.
    const reply = named === "a"
      ? {active_branch_id: "b", branch_id: "a", chat: {provider: "p", model: "X"}}
      : {active_branch_id: "b", chat: {provider: "p", model: "Y-selected"}};
    return {json: async () => reply};
  };
  try {
    // agent_settings_changed from another window's pick on b: the pane
    // rereads its own entry.
    useSessionStore.getState().markSessionAgentSettingsStale();
    await loadSessionAgentSettings(sid);
    assert.match(urls[0], /branch_id=a/, "the read names the pane's branch");
    const state = useSessionStore.getState();
    assert.equal(state.agentSettingsBySession[sid]?.chat?.model, "X", "the pane keeps its branch's model");
    assert.equal(state.composerSettingsBySession[sid]?.active_branch_id, "a", "and stays on its branch");
  } finally { globalThis.fetch = oldFetch; }
});
