/**
 * A sub-agent (a Job in the same session) streams frames stamped with
 * job_id. They build that agent's own transcript for the turn's
 * Sub-agents card and never touch the conversation's rows or bookkeeping.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
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
globalThis.window = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  location: { pathname: "/chat" },
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.requestAnimationFrame = undefined;

const { useSessionStore, selectRow } = await import("../../../lib/session-store/index.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);

const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");
const { agentJobFor, noteJobStatus, ownsSubAgentChatKey, promptBody, releaseSubAgentTranscripts, subAgentChatKey } = await import(
  "../../../lib/chat/transcript/sub-agent-transcripts.ts"
);
const { setPendingUserText, getPendingUserText, clearPendingUserText } = await import(
  "../../../lib/chat/send/pending-user-text.ts"
);

const SID = "s_sub";
const frame = (data) => applyChatWsMessage({ type: "chat_response", data: { session_id: SID, ...data } });

test("a removed session releases its Jobs' continuations, other sessions keep theirs", () => {
  noteJobStatus({ job_id: "rel_b", session_id: "rel_s1", status: "queued", parent_msg_id: "rel_a_user_reply", continues_job_id: "rel_a" });
  noteJobStatus({ job_id: "rel_d", session_id: "rel_s2", status: "queued", parent_msg_id: "rel_c_user_reply", continues_job_id: "rel_c" });
  assert.equal(agentJobFor("rel_b"), "rel_a");
  releaseSubAgentTranscripts("rel_s1");
  assert.equal(agentJobFor("rel_b"), "rel_b");
  assert.equal(agentJobFor("rel_d"), "rel_c");
  assert.equal(ownsSubAgentChatKey("rel_s1", subAgentChatKey("rel_s1", "rel_a")), true);
  assert.equal(ownsSubAgentChatKey("rel_s1", subAgentChatKey("rel_s10", "rel_a")), false);
});

test("removing a conversation drops its sub-agent transcripts, not another's", () => {
  const store = useSessionStore.getState();
  store.upsertConversation({ id: "rm_s", title: "x" });
  rows.putRows("rm_s", [{ id: "rm_u", role: "user", content: "a", status: "done" }]);
  rows.putRows(subAgentChatKey("rm_s", "j1"), [{ id: "rm_j1", role: "user", content: "b", status: "done" }]);
  rows.putRows(subAgentChatKey("rm_s0", "j1"), [{ id: "rm_other", role: "user", content: "c", status: "done" }]);
  store.removeConversation("rm_s");
  const s = useSessionStore.getState();
  assert.equal(s.messageOrder[subAgentChatKey("rm_s", "j1")], undefined);
  assert.equal(rows.readRow(subAgentChatKey("rm_s", "j1"), "rm_j1"), undefined);
  assert.deepEqual(s.messageOrder[subAgentChatKey("rm_s0", "j1")], ["rm_other"]);
});

test("on a sub-agent branch the way back comes from the load, not the graph", () => {
  // A bounded load ships graph: [], so the banner and the Spawned-from card
  // read the server's spawned_branch and return to the conversation's
  // newest turn rather than to the spawning reply.
  const banner = readFileSync(new URL("../../../components/chat/messages/cards/agent-branch-banner.tsx", import.meta.url), "utf8");
  assert.match(banner, /const spawned = conv\?\.spawned_branch;\n\s+if \(spawned !== undefined\) \{\n\s+if \(!spawned\) return null;/);
  assert.match(banner, /backTarget: spawned\.back_target,/);
  const card = readFileSync(new URL("../../../components/chat/messages/cards/spawned-from-card.tsx", import.meta.url), "utf8");
  assert.match(card, /const target = spawned\?\.root_id === msg\.id && spawned\.back_target\n\s+\? spawned\.back_target\n\s+: sf\.callerId;/);
});
