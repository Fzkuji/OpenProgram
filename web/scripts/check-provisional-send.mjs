import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      return {
        url: new URL(`../${specifier.slice(2)}.ts`, import.meta.url).href,
        shortCircuit: true,
      };
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
globalThis.WebSocket = { OPEN: 1 };

const sent = [];
Object.assign(globalThis.window, {
  ws: { readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) },
  currentSessionId: null,
});

const { sendChatMessage } = await import(
  "../components/chat/composer/legacy-send.ts"
);
const { useSessionStore } = await import("../lib/session-store/index.ts");
globalThis.window.__sessionStore = useSessionStore;
const provisional = "local_duplicate_send";

const send = (text) => sendChatMessage({
  text,
  sessionId: provisional,
  thinking: "medium",
  toolsEnabled: true,
  webSearchEnabled: false,
});

assert.equal(send("first"), true);
assert.equal(send("second"), true, "a duplicate UI submit is handled locally");
assert.equal(sent.length, 1, "only one provisional turn may wait for its first ACK");
assert.equal(
  globalThis.window.__pendingUserTextBySession?.[provisional],
  "first",
  "a duplicate submit must not overwrite the text paired with the first ACK",
);
assert.ok(
  useSessionStore.getState().runningTasks[provisional],
  "the provisional chat key becomes running immediately",
);

const throwingDraft = "local_send-throws";
useSessionStore.getState().setRunningTaskFor(throwingDraft, {
  session_id: throwingDraft,
  msg_id: "optimistic",
  started_at: Date.now() / 1000,
});
const sendFailure = new Error("socket closed during send");
const loggedSendErrors = [];
const originalConsoleError = console.error;
console.error = (...args) => loggedSendErrors.push(args);
globalThis.window.ws = {
  readyState: 1,
  send: () => { throw sendFailure; },
};
let throwingResult;
let escapedSendError = null;
try {
  throwingResult = sendChatMessage({
    text: "retry me",
    sessionId: throwingDraft,
    thinking: "medium",
    toolsEnabled: true,
    webSearchEnabled: false,
  });
} catch (err) {
  escapedSendError = err;
} finally {
  console.error = originalConsoleError;
}
assert.equal(
  globalThis.window.__pendingFirstAckBySession?.[throwingDraft],
  undefined,
  "a throwing ws.send must release the provisional first-ACK reservation",
);
assert.equal(
  globalThis.window.__pendingUserTextBySession?.[throwingDraft],
  undefined,
  "a throwing ws.send must release its pending user text",
);
assert.equal(
  useSessionStore.getState().runningTasks[throwingDraft],
  undefined,
  "a throwing ws.send must clear provisional running state",
);
assert.equal(escapedSendError, null, "send failure is reported through false");
assert.equal(throwingResult, false);
assert.equal(
  loggedSendErrors.at(-1)?.at(-1),
  sendFailure,
  "the caught send exception must remain observable",
);

const closingDraft = "local_send-closes";
useSessionStore.getState().setRunningTaskFor(closingDraft, {
  session_id: closingDraft,
  msg_id: "optimistic",
  started_at: Date.now() / 1000,
});
const closingSocket = {
  readyState: 1,
  send() { this.readyState = 3; },
};
globalThis.window.ws = closingSocket;
assert.equal(sendChatMessage({
  text: "retry after close",
  sessionId: closingDraft,
  thinking: "medium",
  toolsEnabled: true,
  webSearchEnabled: false,
}), false);
assert.equal(globalThis.window.__pendingFirstAckBySession?.[closingDraft], undefined);
assert.equal(globalThis.window.__pendingUserTextBySession?.[closingDraft], undefined);
assert.equal(useSessionStore.getState().runningTasks[closingDraft], undefined);

globalThis.window.ws = {
  readyState: 1,
  send: (payload) => sent.push(JSON.parse(payload)),
};

const channelDraft = "local_channel-owner";
globalThis.window.currentSessionId = "real-session-b";
globalThis.window.__pendingChannelChoices = {
  [channelDraft]: { channel: "wechat", account_id: "work" },
};
assert.equal(sendChatMessage({
  text: "channel owner",
  sessionId: channelDraft,
  thinking: "medium",
  toolsEnabled: true,
  webSearchEnabled: false,
}), true);
assert.equal(
  sent.at(-1).channel,
  "wechat",
  "a draft send keeps its captured channel after another real session activates",
);
assert.equal(sent.at(-1).account_id, "work");

const channelDrafts = await import(
  "../lib/runtime-bridge/draft-channel-choice.ts"
);
const acknowledgedChannelHost = {};
channelDrafts.setDraftChannelChoice(acknowledgedChannelHost, channelDraft, {
  channel: "wechat",
  account_id: "work",
});
channelDrafts.dropDraftChannelChoice(
  acknowledgedChannelHost,
  channelDraft,
);
assert.equal(
  channelDrafts.draftChannelChoiceFor(acknowledgedChannelHost, channelDraft),
  null,
);
assert.equal(
  acknowledgedChannelHost._pendingChannelChoice,
  null,
  "ACK cleanup must also clear the matching compatibility pending choice",
);
const legacyChannelHost = {
  _pendingChannelChoice: { channel: "slack", account_id: "team" },
};
channelDrafts.dropDraftChannelChoice(legacyChannelHost, channelDraft, true);
assert.equal(
  legacyChannelHost._pendingChannelChoice,
  null,
  "active legacy first-turn ACK cleanup must clear the unkeyed choice",
);
const backgroundAckHost = {};
channelDrafts.setDraftChannelChoice(backgroundAckHost, "local_background-a", {
  channel: "wechat",
  account_id: "a",
});
channelDrafts.switchDraftChannelChoice(
  backgroundAckHost,
  "local_background-a",
  "local_active-b",
);
channelDrafts.setDraftChannelChoice(backgroundAckHost, "local_active-b", {
  channel: "slack",
  account_id: "b",
});
channelDrafts.dropDraftChannelChoice(
  backgroundAckHost,
  "local_background-a",
  false,
);
assert.deepEqual(
  backgroundAckHost._pendingChannelChoice,
  { channel: "slack", account_id: "b" },
  "background ACK must not clear another draft's global channel choice",
);
const activeAckWithOtherGlobalHost = {};
channelDrafts.setDraftChannelChoice(
  activeAckWithOtherGlobalHost,
  "local_ack-owner",
  { channel: "wechat", account_id: "owner" },
);
activeAckWithOtherGlobalHost._pendingChannelChoice = {
  channel: "slack",
  account_id: "other",
};
channelDrafts.dropDraftChannelChoice(
  activeAckWithOtherGlobalHost,
  "local_ack-owner",
  true,
);
assert.deepEqual(
  activeAckWithOtherGlobalHost._pendingChannelChoice,
  { channel: "slack", account_id: "other" },
  "active ACK must not clear a compatibility choice owned by another draft",
);

const target = await import(
  "../components/chat/composer/modes/fn-form/session-target.ts"
);
assert.equal(target.resolveFnFormSessionId(null, provisional), provisional);
assert.equal(target.resolveFnFormSessionId("real", "real"), "real");
assert.equal(
  typeof target.shouldClearLegacyRunning,
  "function",
  "fn-form completion needs an owner-aware legacy-running guard",
);
assert.equal(
  target.shouldClearLegacyRunning(provisional, "local_other", null),
  false,
  "failure from draft A must not clear draft B's running UI",
);
assert.equal(
  target.shouldClearLegacyRunning("real-a", "real-b", "real-b"),
  false,
  "failure from session A must not clear session B's running task",
);
assert.equal(target.shouldClearLegacyRunning(provisional, provisional, null), true);

const attachmentDb = await import(
  "../components/chat/composer/attach/attach-idb.ts"
);
assert.equal(
  typeof attachmentDb.markAttachmentOwnerClosed,
  "function",
  "closing a draft needs a synchronous attachment-owner tombstone",
);
let closedOwnerNotification = null;
const unsubscribeClosedOwner = attachmentDb.onAttachmentOwnerClosed(
  (ownerKey) => { closedOwnerNotification = ownerKey; },
);
attachmentDb.markAttachmentOwnerClosed("local_closed-attachments");
assert.equal(
  closedOwnerNotification,
  "local_closed-attachments",
  "closing a draft must notify the live attachment cache for preview cleanup",
);
unsubscribeClosedOwner();
assert.equal(
  attachmentDb.attachmentOwnerIsClosed("local_closed-attachments"),
  true,
);
let closedOwnerDbOpens = 0;
globalThis.window.indexedDB = {
  open: () => {
    closedOwnerDbOpens++;
    throw new Error("closed attachment owner must not open IndexedDB");
  },
};
await attachmentDb.saveAttachments("local_closed-attachments", {
  images: [],
  docs: [],
});
assert.equal(
  closedOwnerDbOpens,
  0,
  "a FileReader completion after draft close must not persist its owner",
);

const composer = readFileSync(
  new URL("../components/chat/composer/index.tsx", import.meta.url),
  "utf8",
);
const wsSendBody = composer.slice(
  composer.indexOf("function wsSend("),
  composer.indexOf("const noop"),
);
assert.match(wsSendBody, /try \{[\s\S]*w\.ws\.send/);
assert.match(wsSendBody, /catch \(error\) \{[\s\S]*return false;/);
assert.match(
  composer,
  /const dispatchSessionId = resolveFnFormSessionId\(currentSessionId, activeChatKey\);/,
);
assert.match(composer, /body\.session_id = dispatchSessionId;/);
assert.match(composer, /store\.setRunningTaskFor\(dispatchSessionId,/);
assert.match(composer, /pendingProjectsByChat\[pendingProjectKey\]/);
assert.match(composer, /action:\s*"set_session_project"/);
assert.match(composer, /takePendingProject\(pendingProjectKey\)/);
assert.match(composer, /const shouldActivate = sessionAckIsActive\(sid\);/);
assert.match(composer, /useCenterTabs\.getState\(\)\.markSessionReady\(sid\);/);
assert.match(
  composer,
  /if \(shouldActivate\) \{[\s\S]*setCurrentConv\(sid\);[\s\S]*router\.push/,
);
assert.match(composer, /const submitOwnerKey = activeChatKey \?\? currentSessionId;/);
assert.match(
  composer,
  /const handled = sendChatMessage\([\s\S]*?if \(!handled\) return;[\s\S]*?setComposerInputFor\(submitOwnerKey, ""\)/,
  "Composer must keep its captured text and attachments when WS send fails",
);
assert.match(composer, /setComposerInputFor\(submitOwnerKey, ""\)/);
assert.match(composer, /clearAttachmentsAfterSubmit\(submitOwnerKey\)/);
assert.match(composer, /action:\s*"set_conversation_channel"/);
assert.match(composer, /draftChannelChoiceFor\([^,]+, dispatchSessionId\)/);
assert.match(
  composer,
  /if \(shouldClearLegacyRunning\([\s\S]*?dispatchSessionId,[\s\S]*?store\.activeChatKey,[\s\S]*?store\.currentSessionId,[\s\S]*?\)\) \{\s*w\.setRunning\?\.\(false\);/,
);

const stopBody = composer.slice(
  composer.indexOf("function stop()"),
  composer.indexOf("// Pick a slash command"),
);
assert.match(stopBody, /const targetSessionId = resolveFnFormSessionId\(/);
assert.match(stopBody, /setRunningTaskFor\(targetSessionId, null\)/);
assert.match(stopBody, /action: "stop", session_id: targetSessionId/);

const attachmentHook = readFileSync(
  new URL(
    "../components/chat/composer/attach/use-composer-attachments.ts",
    import.meta.url,
  ),
  "utf8",
);
assert.match(attachmentHook, /clearAfterSubmit = useCallback\(\(ownerKey:/);
assert.match(attachmentHook, /attachmentOwnerIsClosed\(chatKey\)/);
assert.match(attachmentHook, /onAttachmentOwnerClosed\(\(chatKey\) =>/);
assert.match(attachmentHook, /revokeAttachmentPreviews\(closedAttachments\)/);
assert.match(attachmentHook, /const mountedRef = useRef\(true\);/);
assert.match(
  attachmentHook,
  /return \(\) => \{\s*mountedRef\.current = false;[\s\S]*releaseAttachmentPreviews/,
);
assert.match(attachmentHook, /if \(!mountedRef\.current\) \{[\s\S]*releaseAttachmentPreviews/);

const chatHandlers = readFileSync(
  new URL("../lib/runtime-bridge/chat-handlers.ts", import.meta.url),
  "utf8",
);
assert.match(
  chatHandlers,
  /dropDraftChannelChoice\(W, sid, isActive\);/,
  "chat_ack must consume the channel choice carried by the first payload",
);

console.log("provisional send checks passed");
