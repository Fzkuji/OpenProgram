/**
 * Module-boundary regressions: import the real submit, sender, queue, steer,
 * pending-text and paste-store modules. React's callback adapter, Zustand's
 * storage adapter, session/UI ports and network are deterministic test doubles.
 * These tests do not render React or verify browser scrolling/ACK delivery.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { beforeEach, test } from "node:test";
import { fileURLToPath } from "node:url";
import { setImmediate as nextTurn } from "node:timers/promises";

const webRoot = new URL("../../", import.meta.url);
const host = globalThis.__submitReliability = { runtime: {} };
const mocks = {
  react: "export const useCallback = (callback) => callback;",
  zustand: `export function create(init) {
    let state;
    const get = () => state;
    const set = (patch) => { state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }; };
    const store = (select = value => value) => select(state);
    store.getState = get; store.setState = set;
    state = init(set, get, store);
    return store;
  }`,
  "@/lib/session-store": "export const useSessionStore = { getState: () => host.sessions };",
  "@/lib/runtime-bridge/state": "export const runtimeState = host.runtime; export const getSocket = () => host.socket;",
  "@/lib/execution/function-invocation": "export const parseFunctionInvocation = () => ({kind:'none'});",
  "@/lib/abilities/functions-store": "export const useFunctions = {getState: () => ({functions:[]})};",
  "@/lib/format-utils/toast": "export const showToast = (...args) => host.toasts.push(args);",
  "@/lib/chat/attachment-marker": "export const buildAttachmentEnvelope = () => ({mentions:[],imagesPayload:[],docsPayload:[]});",
  "../attach/attachment-session-cache": "export const attachmentsBlockSend = () => null;",
  "../attach/at-mention": "export const expandAtMentions = (text) => host.expandMentions(text);",
  "../modes/fn-form/session-target": "export const resolveFnFormSessionId = (current, active) => current ?? active;",
  "@/lib/chat/chat-scroll": `export const defaultScrollerKey = (sid, peer) => peer ? 'peer:' + sid : sid;
    export const noteTakeLatest = note => host.notes.push(note);`,
  "@/lib/prefs/theme-pref": "export const traceThemeEvent = () => {};",
  "@/lib/desktop/desktop-bridge": "export const surfaceOriginForChat = () => null;",
  "@/lib/runtime-bridge/draft-channel-choice": "export const draftChannelChoiceHost = {}; export const draftChannelChoiceFor = () => null;",
  "@/lib/runtime-bridge/helpers": `export const setWelcomeVisible = value => {
    host.welcomeWrites.push(value); host.sessions.welcomeVisible = value;
  };`,
  "@/lib/runtime-bridge/ui": `export const setRunning = value => {
    host.globalRunWrites.push(value); host.runtime.isRunning = value;
    const sid = host.runtime.currentSessionId;
    if (sid && !host.sessions.runningTasks[sid]) {
      host.sessions.setRunningTaskFor(sid, {session_id:sid,msg_id:''});
    }
  };`,
  "@/lib/net/chat-stream": `export const appendLocalUserTurn = (sid, id, text, display, timestamp, status) => {
    host.sessions.messagesById[id] = {id, role:'user', content:text, status};
    (host.sessions.messageOrder[sid] ??= []).push(id);
  };`,
  "@/lib/net/execution-client": `export class ExecutionApiError extends Error {}
    export const getExecutionSnapshot = async (eid, signal, sid) => ({
      session_id:sid, execution_id:eid, status_version:7, status:'running', capabilities:{steer:true}
    });
    export const postExecutionCommand = async command => {
      host.commands.push(command); if (host.postCommand) return host.postCommand(command); return {command_id:command.command_id,status:'applied'};
    };`,
};
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (Object.hasOwn(mocks, specifier)) {
      return {
        url: "data:text/javascript," + encodeURIComponent(
          "const host = globalThis.__submitReliability;\n" + mocks[specifier],
        ),
        shortCircuit: true,
      };
    }
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        const url = base + suffix;
        if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

globalThis.window = {};
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.WebSocket = { OPEN: 1 };
const { useChatSubmit, stopSession } = await import("../../components/chat/composer/submit/use-chat-submit.ts");
const { sendChatMessage } = await import("../../components/chat/composer/submit/send-chat-message.ts");
const { useSendQueue, queueFor } = await import("../../lib/chat/send-queue.ts");
const { pasteStore, placeholderToken } = await import("../../components/chat/composer/paste/paste-store.ts");
const pending = await import("../../lib/chat/pending-user-text.ts");

function focus(sid) {
  host.sessions.activeChatKey = sid;
  host.sessions.currentSessionId = sid?.startsWith("local_") ? null : sid;
  host.runtime.currentSessionId = host.sessions.currentSessionId;
}
function running(sid = "A", { provisional = false, replyId = "exec-A" } = {}) {
  const task = provisional
    ? { session_id: sid, msg_id: "" }
    : { session_id: sid, msg_id: "user-A", execution_id: "exec-A", status_version: 7 };
  host.sessions.runningTasks[sid] = task;
  host.sessions.messagesById[replyId] = { id: replyId, role: "assistant", status: "streaming", content: "partial" };
  host.sessions.messageOrder[sid] = [replyId];
  return task;
}
function composer(input, overrides = {}) {
  host.sessions.composerDrafts.A = input;
  return useChatSubmit({
    bound: null, input, activeChatKey: "A", currentSessionId: "A", isRunning: true,
    noEnabledModels: false, promptNeedModel() {}, send: () => true,
    setComposerInputFor(owner, value) {
      host.draftWrites.push([owner, value]);
      host.sessions.composerDrafts[owner] = value;
      if (host.collectPastesOnClear && value === "") pasteStore.retainOnly(new Set());
    },
    setHistoryIndex() {}, slash: { runCommand: () => false, close() {} },
    pendingImages: [], pendingDocs: [], clearAttachmentsAfterSubmit() {},
    thinking: "medium", toolsEnabled: true, toolsProfile: "__agent__",
    webSearchEnabled: false, fastEnabled: false, fastSupported: false,
    runningMessageMode: "queue", dispatchFunction: () => false,
    ...overrides,
  });
}
function sendArgs(overrides = {}) {
  return { text: "hello", sessionId: "A", thinking: "medium", toolsEnabled: true, webSearchEnabled: false, ...overrides };
}

beforeEach(() => {
  for (const sid of ["A", "B", "local_new"]) {
    pending.clearPendingUserText(sid); pending.clearPendingFirstAck(sid);
  }
  pasteStore.retainOnly(new Set());
  useSendQueue.setState({ queues: {} });
  Object.assign(host.runtime, { currentSessionId: "A", isRunning: false, _optimisticCancels: {}, _optimisticStops: {} });
  Object.assign(host, {
    postCommand: undefined, frames: [], commands: [], notes: [], toasts: [], draftWrites: [], runWrites: [],
    globalRunWrites: [], welcomeWrites: [], collectPastesOnClear: false,
    expandMentions: async text => ({ text }),
  });
  host.sessions = {
    activeChatKey: "A", currentSessionId: "A", welcomeVisible: true,
    runningTasks: {}, messagesById: {}, messageOrder: {}, composerDrafts: {},
    pendingProjectsByChat: {}, composerSettingsBySession: {}, additionalWorkingDirsBySession: {},
    setRunningTaskFor(sid, task) {
      host.runWrites.push([sid, task]);
      if (task) this.runningTasks[sid] = task; else delete this.runningTasks[sid];
    },
    updateMessage(sid, id, patch) { Object.assign(this.messagesById[id], patch); },
  };
  host.socket = { readyState: 1, send: wire => host.frames.push(JSON.parse(wire)) };
});

test("queue snapshots full paste before draft cleanup; drain survives paste GC", async () => {
  running();
  const content = "  const result = '完整正文';\n".repeat(120);
  const entry = pasteStore.add(content);
  host.collectPastesOnClear = true;
  await composer(`explain\n${placeholderToken(entry)}`).submit();
  assert.equal(pasteStore.get(entry.id), undefined);
  assert.equal(queueFor("A")[0].text, `explain\n${content}`);
  assert.equal(host.frames.length, 0);
  assert.deepEqual(host.notes, [{ sessionId: "A", scrollerKey: "A", turnSeed: `queue:${queueFor("A")[0].id}` }]);
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  assert.equal(host.frames[0].text, `explain\n${content}`);
});

test("steer posts expanded paste, not its placeholder", async () => {
  running();
  const content = "look at this code\n".repeat(150);
  await composer(placeholderToken(pasteStore.add(content)), { runningMessageMode: "steer" }).submit();
  await nextTurn();
  assert.equal(host.commands[0]?.payload.message, content);
  assert.equal(host.frames.length, 0);
  assert.equal(queueFor("A").length, 0);
  assert.equal(host.notes.length, 1);
});

test("steer length guard measures expanded content and retains it for queue drain", async () => {
  running();
  const content = "x".repeat(5000);
  await composer(placeholderToken(pasteStore.add(content)), { runningMessageMode: "steer" }).submit();
  await nextTurn();
  assert.equal(host.commands.length, 0);
  assert.equal(queueFor("A")[0].text, content);
  assert.equal(queueFor("A")[0].steerError, "too_long");
});

for (const mode of ["queue", "steer"]) {
  test(`${mode}: missing paste keeps draft and emits no follow request`, async () => {
    running();
    const token = placeholderToken(pasteStore.add("lost content"));
    pasteStore.retainOnly(new Set());
    await composer(token, { runningMessageMode: mode }).submit();
    await nextTurn();
    assert.equal(queueFor("A").length, 0);
    assert.equal(host.draftWrites.length, 0);
    assert.equal(host.notes.length, 0);
    assert.equal(host.commands.length, 0);
  });
}
for (const field of ["pendingImages", "pendingDocs"]) {
  test(`${field}: rejected queue submit keeps draft and emits no follow request`, async () => {
    running();
    await composer("caption", { [field]: [{ id: "attachment" }] }).submit();
    assert.equal(queueFor("A").length, 0);
    assert.equal(host.draftWrites.length, 0);
    assert.equal(host.notes.length, 0);
  });
}

test("peer queue submit emits follow only for its own scroller; repeated sends have distinct seeds", async () => {
  focus("B"); running();
  await composer("first", { bound: "A" }).submit();
  await composer("second", { bound: "A" }).submit();
  assert.deepEqual(host.notes.map(n => [n.sessionId, n.scrollerKey]), [["A", "peer:A"], ["A", "peer:A"]]);
  assert.notEqual(host.notes[0].turnSeed, host.notes[1].turnSeed);
  assert.equal(host.sessions.runningTasks.B, undefined);
});

test("ordinary submit still expands paste before transport", async () => {
  const content = "body\n".repeat(500);
  await composer(placeholderToken(pasteStore.add(content)), { isRunning: false }).submit();
  assert.equal(host.frames[0].text, content);
});

test("closed socket preserves ordinary draft and emits no follow request", async () => {
  host.socket.readyState = 3;
  await composer("retry me", { isRunning: false }).submit();
  assert.equal(host.draftWrites.length, 0);
  assert.equal(host.notes.length, 0);
  assert.equal(host.frames.length, 0);
});

for (const outcome of ["false", "throw"]) {
  for (const replyId of ["exec-A", "user-A_reply"]) {
    test(`cancel ${outcome}, ${replyId}: retains running task, reply and queue`, async () => {
      const task = running("A", { replyId });
      await composer("next instruction").submit();
      const before = queueFor("A")[0];
      assert.doesNotThrow(() => stopSession("A", () => {
        if (outcome === "throw") throw new Error("disconnected");
        return false;
      }));
      assert.equal(host.sessions.runningTasks.A, task);
      assert.equal(host.sessions.messagesById[replyId].status, "streaming");
      assert.equal(host.runWrites.length, 0);
      assert.equal(Object.keys(host.runtime._optimisticCancels).length, 0);
      useSendQueue.getState().drain("A");
      assert.equal(queueFor("A")[0], before);
      assert.equal(host.frames.length, 0);
      assert.equal(host.toasts.length, 1);
    });
  }
}

test("successful cancel retains the existing optimistic rollback record", () => {
  const task = running();
  const sent = [];
  stopSession("A", payload => { sent.push(payload); return true; });
  assert.equal(sent[0].action, "execution.cancel");
  assert.equal(sent[0].execution_id, task.execution_id);
  assert.equal(host.sessions.runningTasks.A, undefined);
  assert.equal(host.runtime._optimisticCancels[sent[0].command_id].previousMessageStatus, "streaming");
});

test("pre-ACK stop still records the deferred stop without sending an unidentified cancel", () => {
  running("A", { provisional: true });
  stopSession("A", () => assert.fail("no identified execution to cancel yet"));
  assert.equal(host.runtime._optimisticStops.A.messageId, "exec-A");
  assert.equal(host.sessions.runningTasks.A, undefined);
});

for (const focused of ["B", "local_new"]) {
  test(`A queue draining while ${focused} is focused leaves that view unchanged`, async () => {
    running();
    await composer("A follow-up").submit();
    assert.equal(queueFor("A")[0].background, false);
    focus(focused);
    delete host.sessions.runningTasks.A;
    useSendQueue.getState().drain("A");
    assert.equal(host.frames[0].session_id, "A");
    assert.equal(host.frames[0].text, "A follow-up");
    assert.equal(host.sessions.runningTasks.A.session_id, "A");
    assert.equal(host.sessions.runningTasks[focused], undefined);
    assert.equal(host.globalRunWrites.length, 0);
    assert.equal(host.welcomeWrites.length, 0);
    assert.equal(host.sessions.welcomeVisible, true);
  });
}

test("a delayed mention expansion sends to its captured session without altering the newly focused view", async () => {
  let complete;
  host.expandMentions = () => new Promise(resolve => { complete = resolve; });
  const sending = composer("@file explain", { isRunning: false }).submit();
  focus("B");
  complete({ text: "expanded A file" });
  await sending;
  assert.equal(host.frames[0].session_id, "A");
  assert.equal(host.globalRunWrites.length, 0);
  assert.equal(host.welcomeWrites.length, 0);
  assert.equal(host.sessions.runningTasks.B, undefined);
});

test("onSent focus change is rechecked before global running state is touched", () => {
  sendChatMessage(sendArgs({ onSent: () => focus("B") }));
  assert.equal(host.frames[0].session_id, "A");
  assert.equal(host.globalRunWrites.length, 0);
  assert.equal(host.sessions.runningTasks.B, undefined);
});

test("focused ordinary send still hides welcome and sets running", () => {
  assert.equal(sendChatMessage(sendArgs()), true);
  assert.deepEqual(host.welcomeWrites, [false]);
  assert.deepEqual(host.globalRunWrites, [true]);
});

test("focused provisional send still hides welcome and reserves its own task", () => {
  focus("local_new");
  assert.equal(sendChatMessage(sendArgs({ sessionId: "local_new" })), true);
  assert.deepEqual(host.welcomeWrites, [false]);
  assert.deepEqual(host.globalRunWrites, [true]);
  assert.equal(host.sessions.runningTasks.local_new.session_id, "local_new");
});

test("explicit peer send never writes focused-shell state, even for the same session", () => {
  sendChatMessage(sendArgs({ background: true }));
  assert.equal(host.globalRunWrites.length, 0);
  assert.equal(host.welcomeWrites.length, 0);
});

test("stale legacy focus cannot route setRunning to another session", () => {
  host.runtime.currentSessionId = "B";
  sendChatMessage(sendArgs());
  assert.equal(host.sessions.runningTasks.B, undefined);
  assert.equal(host.globalRunWrites.length, 0);
});

test("current chat key overrides a stale legacy currentSessionId", () => {
  host.sessions.activeChatKey = "B";
  host.sessions.currentSessionId = "B";
  sendChatMessage(sendArgs());
  assert.equal(host.globalRunWrites.length, 0);
  assert.equal(host.welcomeWrites.length, 0);
});

test("lost steering acknowledgement confirms the same command automatically", async t => {
  running();
  const timers = [];
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => { timers.push({callback,delay}); return timers.length; });
  t.mock.method(globalThis, 'clearTimeout', () => {});
  host.postCommand = command => {
    if (host.commands.length === 1) throw new Error('response lost after server accepted');
    return {command_id:command.command_id,status:'applied'};
  };
  await composer('keep this instruction', {runningMessageMode:'steer'}).submit();
  await nextTurn();
  assert.equal(queueFor('A')[0].steerError, 'unconfirmed');
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 1500);
  timers.shift().callback();
  await nextTurn();
  assert.equal(host.commands.length, 2);
  assert.deepEqual(host.commands[1], host.commands[0]);
  assert.equal(queueFor('A').length, 0);
  assert.equal(host.frames.length, 0);
});

test("unconfirmed steering backs off with one timer and preserves its command", async t => {
  running();
  let nextId = 0;
  const timers = new Map();
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => { const id=++nextId; timers.set(id,{callback,delay}); return id; });
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id));
  host.postCommand = () => { throw new Error('offline'); };
  await composer('pending instruction',{runningMessageMode:'steer'}).submit();
  await nextTurn();
  const firstCommand=host.commands[0];
  for (const expected of [1500,3000,6000,12000,24000,30000,30000]) {
    assert.equal(timers.size,1);
    const [id,timer]=[...timers][0];
    assert.equal(timer.delay,expected);
    timers.delete(id); timer.callback(); await nextTurn();
    assert.equal(queueFor('A')[0].text,'pending instruction');
    assert.deepEqual(host.commands.at(-1),firstCommand);
    assert.equal(host.frames.length,0);
  }
  // A manual confirmation replaces the pending timer, never creates a second loop.
  host.postCommand = command => ({command_id:command.command_id,status:'applied'});
  const {steerQueuedMessage}=await import('../../lib/chat/steer-message.ts');
  await steerQueuedMessage('A',queueFor('A')[0].id);
  assert.equal(timers.size,0);
  assert.equal(queueFor('A').length,0);
});
