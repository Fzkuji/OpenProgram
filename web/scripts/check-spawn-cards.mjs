/**
 * Live spawn cards — the `sub_agent` stream event must build the same
 * `attachCards` shape the reload path (conv-mapper) builds from the DAG's
 * `function === "attach"` rows. If the two drift, a spawn renders one way
 * mid-stream and another way after a refresh.
 *
 * Also guards the execution strip's streaming default-open behaviour: an
 * assistant that is working must show its steps without a click.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../${specifier.slice(2)}`, import.meta.url);
      // `@/lib/session-store` is a directory with an index.ts; other
      // aliases point straight at a .ts file.
      const file = new URL(`${base.pathname}.ts`, base);
      const url = existsSync(file) ? file : new URL(`${base.pathname}/index.ts`, base);
      return { url: url.href, shortCircuit: true };
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

const { useSessionStore } = await import("../lib/session-store/index.ts");
const { applyChatWsMessage } = await import("../lib/net/chat-stream.ts");

const SID = "s1";
// Envelopes key off the USER turn id; the assistant reply bubble the
// reducer writes into is `${msg_id}_reply`.
const UID = "u1";
const RID = `${UID}_reply`;

function reply() {
  return useSessionStore.getState().messagesById[RID];
}

function send(event) {
  applyChatWsMessage({
    type: "chat_response",
    data: { type: "stream_event", session_id: SID, msg_id: UID, event },
  });
}

useSessionStore.setState({ messagesById: {}, messageOrder: {} });
applyChatWsMessage({
  type: "chat_ack",
  data: { session_id: SID, msg_id: UID },
});

// The spawning tool call, then the spawn announcing itself as running.
send({ type: "tool_use", tool: "task", tool_call_id: "tc_1", input: "{}" });
send({
  type: "sub_agent",
  card_id: "card_a",
  tool_call_id: "tc_1",
  agent_id: "worker",
  content: "",
  attach: {
    session_id: SID,
    head_id: null,
    label: "probe",
    prompt: "do the thing",
    status: "running",
  },
});

let cards = reply().attachCards;
assert.equal(cards.length, 1, "running spawn must create a card immediately");
assert.equal(cards[0].id, "card_a");
assert.equal(cards[0].function, "attach", "SubAgentStep routes on function=attach");
assert.equal(cards[0].display, "runtime");
assert.equal(cards[0].status, "running");
assert.equal(cards[0].attach.status, "running");
assert.equal(cards[0].attach.label, "probe");
assert.equal(cards[0].calledBy, RID, "card must anchor to the caller turn");

// Terminal event for the SAME spawn patches in place rather than
// appending — otherwise one spawn renders as two cards.
send({
  type: "sub_agent",
  card_id: "card_a",
  tool_call_id: "tc_1",
  agent_id: "worker",
  content: "the answer",
  attach: {
    session_id: SID,
    head_id: "head_9",
    label: "probe",
    prompt: "do the thing",
    status: "completed",
  },
});

cards = reply().attachCards;
assert.equal(cards.length, 1, "terminal event must patch, not append");
assert.equal(cards[0].status, "done");
assert.equal(cards[0].attach.status, "completed");
assert.equal(cards[0].attach.head_id, "head_9", "Switch ↗ needs the head id");
assert.equal(cards[0].content, "the answer");

// A second, distinct spawn is a second card.
send({
  type: "sub_agent",
  card_id: "card_b",
  tool_call_id: "tc_2",
  attach: { session_id: SID, label: "second", status: "running" },
});
assert.equal(reply().attachCards.length, 2, "distinct spawns are distinct cards");

// An errored spawn must not stay stuck in the running state.
send({
  type: "sub_agent",
  card_id: "card_b",
  tool_call_id: "tc_2",
  content: "RuntimeError: boom",
  attach: { session_id: SID, label: "second", status: "errored" },
});
const errored = reply().attachCards.find((c) => c.id === "card_b");
assert.equal(errored.status, "error");
assert.equal(errored.attach.status, "errored");

// Events without a card_id are ignored rather than creating a card the
// terminal event can never find again.
const before = reply().attachCards.length;
send({ type: "sub_agent", attach: { status: "running" } });
assert.equal(reply().attachCards.length, before, "card_id is required");

// finalize() overwrites `blocks` from the server's authoritative list;
// it must NOT drop the cards the live path accumulated.
applyChatWsMessage({
  type: "chat_response",
  data: {
    type: "result",
    session_id: SID,
    msg_id: UID,
    content: "done",
    blocks: [{ type: "text", text: "done" }],
  },
});
assert.equal(
  reply().attachCards.length,
  2,
  "finalize must not clobber live attachCards",
);
assert.equal(reply().status, "done");

// --- execution strip: visible while the assistant works ------------------
const strip = readFileSync(
  new URL("../components/chat/messages/execution-strip.tsx", import.meta.url),
  "utf8",
);
// Scope to ExecutionStrip's own body — `Collapse` and `StepRow` further
// down the file keep their own unrelated useState(false) toggles.
const stripBody = strip.slice(
  strip.indexOf("export function ExecutionStrip"),
  strip.indexOf("/** 后端 ensure_ascii"),
);
assert.ok(stripBody.length > 0, "failed to locate the ExecutionStrip body");
assert.match(
  stripBody,
  /const\s+open\s*=\s*userSet\s*\?\?\s*!!streaming/,
  "streaming turns must default to expanded, with a manual toggle winning",
);
assert.doesNotMatch(
  stripBody,
  /useState\(false\)/,
  "the old always-collapsed default must be gone",
);

console.log("spawn-card checks passed");
