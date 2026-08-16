import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { shouldHydrateTranscriptForTreeUpdate } from "../lib/runtime-bridge/transcript-hydration.ts";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const handlers = source("lib/runtime-bridge/chat-handlers.ts");
const design = source(
  "../docs/reference/design/integrations/web-use.html",
);

const hydrateStart = handlers.indexOf(
  "function hydrateTranscriptForTreeUpdate",
);
const hydrateEnd = handlers.indexOf(
  "export function handleRunningTaskClear",
  hydrateStart,
);
assert.ok(
  hydrateStart >= 0 && hydrateEnd > hydrateStart,
  "tree-update hydrator must exist",
);
const hydrate = handlers.slice(hydrateStart, hydrateEnd);

assert.ok(
  hydrate.indexOf("shouldHydrateTranscriptForTreeUpdate") >= 0 &&
    hydrate.indexOf("shouldHydrateTranscriptForTreeUpdate") <
      hydrate.indexOf('action: "load_session"'),
  "the tested hydration decision must guard the handler's load_session",
);

function createState(messagesById, messageOrder, hydratedPaths = new Set()) {
  return {
    currentSessionId: "session-a",
    sessionId: "session-a",
    path: "tool-node",
    messagesById,
    messageOrder,
    hydratedPaths,
  };
}

let loadSessionSends = 0;
const liveChat = createState(
  {
    reply: { role: "assistant", display: "normal", status: "running" },
  },
  { "session-a": ["reply"] },
);
if (shouldHydrateTranscriptForTreeUpdate(liveChat)) loadSessionSends++;
assert.equal(
  loadSessionSends,
  0,
  "a live normal chat reply must suppress hydration",
);
assert.equal(
  liveChat.hydratedPaths.size,
  0,
  "suppressed chat updates must not consume the path",
);

loadSessionSends = 0;
const standalone = createState(
  {
    runtime: { role: "assistant", display: "runtime", status: "running" },
  },
  { "session-a": ["runtime"] },
);
if (shouldHydrateTranscriptForTreeUpdate(standalone)) loadSessionSends++;
if (shouldHydrateTranscriptForTreeUpdate(standalone)) loadSessionSends++;
assert.equal(
  loadSessionSends,
  1,
  "a standalone runtime run must hydrate exactly once per path",
);

loadSessionSends = 0;
const existingCard = createState(
  { "tool-node": { role: "assistant", display: "runtime", status: "running" } },
  { "session-a": ["tool-node"] },
);
if (shouldHydrateTranscriptForTreeUpdate(existingCard)) loadSessionSends++;
assert.equal(
  loadSessionSends,
  0,
  "an existing runtime card must not reload the transcript",
);

assert.match(design, /Chat 渲染稳定性/);
assert.match(design, /历史 message element identity 保持不变/);

console.log("web-use chat stability contract: ok");
