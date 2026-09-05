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
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../${specifier.slice(2)}`, import.meta.url).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    // Extensionless relative imports between source modules (Node needs the
    // extension; TypeScript and the Next build resolve them on their own).
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      // Append to href, not to pathname: pathname is percent-encoded and
      // re-parsing it against the same base double-encodes any space in the
      // repo path.
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

// A function invoked by this assistant sends tree_update with the assistant
// id as its owner. It must enrich the existing Functions timeline, never
// create a second top-level runtime row below the assistant message.
send({
  type: "tool_use",
  tool: "web_use",
  tool_call_id: "tc_web_use",
  input: '{"command":"list_pages"}',
});
applyChatWsMessage({
  type: "chat_response",
  data: {
    type: "tree_update",
    session_id: SID,
    msg_id: RID,
    function: "web_use",
    tree: { path: "web-use-running", name: "web_use", status: "running" },
  },
});
assert.equal(
  useSessionStore.getState().messagesById[`${RID}_reply`],
  undefined,
  "an agent-owned function tree must not create a standalone runtime row",
);
assert.deepEqual(
  reply().callRoots,
  [{ path: "web-use-running", name: "web_use", status: "running" }],
  "the function tree must attach to the owning assistant timeline",
);
applyChatWsMessage({
  type: "chat_response",
  data: {
    type: "tree_update",
    session_id: SID,
    msg_id: RID,
    function: "web_use",
    tree: { path: "web-use-node-1", name: "web_use", status: "completed" },
  },
});
assert.deepEqual(
  reply().callRoots,
  [{ path: "web-use-node-1", name: "web_use", status: "completed" }],
  "the terminal exact tree must replace its synthetic running tree",
);

// The spawning tool call, then the spawn announcing itself as running.
send({ type: "tool_use", tool: "task", tool_call_id: "tc_1", input: "{}" });
const runStartedAt = reply().timestamp;
assert.ok(
  Number.isFinite(runStartedAt),
  "a live runtime reply must record its start timestamp",
);
const realDateNow = Date.now;
let subAgentNow = 5_000;
Date.now = () => subAgentNow;
try {
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
} finally {
  Date.now = realDateNow;
}

let cards = reply().attachCards;
assert.equal(cards.length, 1, "running spawn must create a card immediately");
assert.equal(cards[0].id, "card_a");
assert.equal(cards[0].function, "attach", "SubAgentStep routes on function=attach");
assert.equal(cards[0].display, "runtime");
assert.equal(cards[0].status, "running");
assert.equal(cards[0].attach.status, "running");
assert.equal(cards[0].attach.label, "probe");
assert.equal(cards[0].calledBy, RID, "card must anchor to the caller turn");
assert.equal(cards[0].timestamp, 5_000, "a sub-agent card owns its first visible time");
const subAgentStartedAt = cards[0].timestamp;

// Terminal event for the SAME spawn patches in place rather than
// appending — otherwise one spawn renders as two cards.
subAgentNow = 9_000;
Date.now = () => subAgentNow;
try {
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
} finally {
  Date.now = realDateNow;
}

cards = reply().attachCards;
assert.equal(cards.length, 1, "terminal event must patch, not append");
assert.equal(cards[0].status, "done");
assert.equal(cards[0].attach.status, "completed");
assert.equal(cards[0].attach.head_id, "head_9", "Switch ↗ needs the head id");
assert.equal(cards[0].content, "the answer");
assert.equal(
  cards[0].timestamp,
  subAgentStartedAt,
  "a sub-agent terminal event must preserve its first visible time",
);

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
assert.equal(
  reply().timestamp,
  runStartedAt,
  "completing a runtime reply must preserve its start timestamp",
);

// A dispatcher-owned runtime row uses status/result envelopes rather than
// the ordinary reply id. Its terminal result must not replace the time at
// which the visible run first appeared.
const runtimeId = "runtime_timestamp";
const originalNow = Date.now;
let fakeNow = 1_700_000_001_000;
Date.now = () => fakeNow;
try {
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "status",
      session_id: SID,
      msg_id: runtimeId,
      display: "runtime",
      function: "web_use",
      status: "running",
    },
  });
  const dispatcherStartedAt = useSessionStore.getState().messagesById[runtimeId].timestamp;
  fakeNow += 1_000;
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "result",
      session_id: SID,
      msg_id: runtimeId,
      display: "runtime",
      function: "web_use",
      content: "done",
    },
  });
  assert.equal(
    useSessionStore.getState().messagesById[runtimeId].timestamp,
    dispatcherStartedAt,
    "a runtime result must not replace the status timestamp",
  );

  const nestedRuntimeId = "nested_runtime_timestamp";
  fakeNow += 1_000;
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "status",
      session_id: SID,
      msg_id: nestedRuntimeId,
      predecessor: RID,
      display: "runtime",
      function: "web_use",
      status: "running",
    },
  });
  const nestedStartedAt = reply().runtimeChildren.find(
    (child) => child.id === nestedRuntimeId,
  ).timestamp;
  fakeNow += 5_000;
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "status",
      session_id: SID,
      msg_id: nestedRuntimeId,
      predecessor: RID,
      display: "runtime",
      function: "web_use",
      status: "running",
    },
  });
  assert.equal(
    reply().runtimeChildren.find((child) => child.id === nestedRuntimeId).timestamp,
    nestedStartedAt,
    "a duplicate nested status must preserve the first timestamp",
  );

  const futureUid = "runtime_future_parent";
  const futureParentId = `${futureUid}_reply`;
  const migratingRuntimeId = "migrating_runtime_timestamp";
  fakeNow += 1_000;
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "status",
      session_id: SID,
      msg_id: migratingRuntimeId,
      predecessor: futureParentId,
      display: "runtime",
      function: "web_use",
      status: "running",
    },
  });
  const migratingStartedAt = useSessionStore.getState()
    .messagesById[migratingRuntimeId].timestamp;
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "stream_event",
      session_id: SID,
      msg_id: futureUid,
      event: { type: "text", text: "parent" },
    },
  });
  fakeNow += 5_000;
  applyChatWsMessage({
    type: "chat_response",
    data: {
      type: "result",
      session_id: SID,
      msg_id: migratingRuntimeId,
      predecessor: futureParentId,
      display: "runtime",
      function: "web_use",
      content: "done",
    },
  });
  const migrated = useSessionStore.getState().messagesById[futureParentId]
    .runtimeChildren.find((child) => child.id === migratingRuntimeId);
  assert.equal(
    migrated.timestamp,
    migratingStartedAt,
    "moving a runtime row under its assistant must preserve the start timestamp",
  );
  assert.equal(
    useSessionStore.getState().messagesById[migratingRuntimeId],
    undefined,
    "the top-level copy must be removed after migration",
  );
} finally {
  Date.now = originalNow;
}

// --- execution strip: visible while the assistant works ------------------
const strip = readFileSync(
  new URL("../components/chat/messages/execution-strip.tsx", import.meta.url),
  "utf8",
);
const assistantBubble = readFileSync(
  new URL("../components/chat/messages/assistant-bubble.tsx", import.meta.url),
  "utf8",
);
const messageList = readFileSync(
  new URL("../components/chat/messages/message-list.tsx", import.meta.url),
  "utf8",
);
const spawnedFromCard = readFileSync(
  new URL("../components/chat/messages/spawned-from-card.tsx", import.meta.url),
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
  /const\s+\[open,\s*setOpen\]\s*=\s*useState\(false\)/,
  "execution traces must default to collapsed, with a manual toggle winning",
);

// Every assistant-owned spawn uses the ordinary timeline row, including
// legacy/mid-stream records that cannot be paired with a tool block. Returning
// from the child branch opens the exact strip and highlights that same row.
assert.match(
  assistantBubble,
  /const attachFifo = \(msg\.attachCards \?\? \[\]\)\.filter\(\(card\) =>\s*!card\.attach\?\.manual/s,
  "same-session spawned agents must enter the timeline-row queue",
);
assert.match(
  assistantBubble,
  /const externalAttachCards = \(msg\.attachCards \?\? \[\]\)\.filter\(\(card\) =>\s*card\.attach\?\.manual/s,
  "manual and cross-session attach cards must retain their separate UI",
);
assert.doesNotMatch(
  assistantBubble,
  /attachFifo\.map\([\s\S]{0,240}<AttachCard\b/,
  "spawn fallback rows must not render AttachCard",
);
assert.match(
  assistantBubble,
  /subagentHeads=\{spawnHeads\(/,
  "each execution strip must expose the sub-agent heads it owns",
);
assert.match(
  strip,
  /data-head-id=\{dataHeadId\}/,
  "SubAgentStep must keep the branch head on its timeline-row DOM node",
);
assert.match(
  messageList,
  /\.tl\[data-subagent-heads~=/,
  "return navigation must open the exact strip containing the sub-agent",
);
assert.match(messageList, /strip\.querySelector\('\.tl-toggle'\)/);
assert.match(
  messageList,
  /\.tl-step\[data-head-id=/,
  "return navigation must reveal the timeline row rather than an attach card",
);
assert.match(
  spawnedFromCard,
  /runtimeState\._pendingExpandAttach\s*=\s*\{\s*head:\s*firstReply,\s*anchor:\s*sf\.callerId/s,
  "the exact-caller return must reveal the same parent timeline row",
);

console.log("spawn-card checks passed");
