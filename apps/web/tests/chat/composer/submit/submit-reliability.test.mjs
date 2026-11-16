/**
 * Module-boundary regressions: import the real submit, sender, queue, steer,
 * pending-text and paste-store modules. React's callback adapter, Zustand's
 * storage adapter, session/UI ports and network are deterministic test doubles.
 * These tests do not render React or verify browser scrolling/ACK delivery.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { after, beforeEach, test } from "node:test";
import { fileURLToPath } from "node:url";
import { setImmediate as nextTurn } from "node:timers/promises";

const webRoot = new URL("../../../../", import.meta.url);
const host = globalThis.__submitReliability = { runtime: {}, storeListeners: new Set() };
const mocks = {
  react: `export const useCallback = (callback) => callback;
    export const useEffect = () => {}; export const useLayoutEffect = () => {};
    export const useState = (value) => [typeof value === "function" ? value() : value, () => {}];`,
  zustand: `export function create(init) {
    let state;
    const get = () => state;
    const listeners = new Set();
    const set = (patch) => { const prev = state; state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }; for (const l of listeners) l(state, prev); };
    const store = (select = value => value) => select(state);
    store.getState = get; store.setState = set; store.subscribe = l => (listeners.add(l), () => listeners.delete(l));
    state = init(set, get, store);
    return store;
  }`,
  "@/lib/session-store": `export const useSessionStore = { getState: () => host.sessions,
    subscribe: (listener) => { host.storeListeners.add(listener); return () => host.storeListeners.delete(listener); } };
    export const selectViewRows = () => (state) => state.rowsById;`,
  "@/lib/runtime-bridge/state": "export const runtimeState = host.runtime; export const getSocket = () => host.socket;",
  "@/lib/execution/function-invocation": "export const parseFunctionInvocation = () => ({kind:'none'});",
  "@/lib/abilities/functions-store": "export const useFunctions = {getState: () => ({functions:[]})};",
  "@/lib/format-utils/toast": "export const showToast = (...args) => host.toasts.push(args);",
  "../attach/attachment-session-cache": "export const attachmentsBlockSend = () => null;",
  "../attach/mention/at-mention": "export const expandAtMentions = (text) => host.expandMentions(text);",
  "../modes/fn-form/session-target": "export const resolveFnFormSessionId = (current, active) => current ?? active;",
  "@/lib/chat/history/chat-scroll": `export const defaultScrollerKey = (sid, peer) => peer ? 'peer:' + sid : sid;
    export const noteTakeLatest = note => host.notes.push(note);`,
  "@/lib/prefs/appearance/theme-pref": "export const traceThemeEvent = () => {};",
  "@/lib/desktop/desktop-bridge": "export const surfaceOriginForChat = () => null;",
  "@/lib/runtime-bridge/actions/draft-channel-choice": "export const draftChannelChoiceHost = {}; export const draftChannelChoiceFor = () => null;",
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
  "@/lib/net/ws/chat-stream": `export const appendLocalUserTurn = (sid, id, text, display, timestamp, status) => {
    host.sessions.rowsById[id] = {id, role:'user', content:text, status};
    (host.sessions.messageOrder[sid] ??= []).push(id);
  };`,
  "@/lib/net/execution/execution-client": `export class ExecutionApiError extends Error {}
    export const getExecutionSnapshot = async (eid, signal, sid) => host.snapshot ? host.snapshot(eid, sid) : ({
      session_id:sid, execution_id:eid, status_version:7, status:'running', capabilities:{steer:true}
    });
    export const postExecutionCommand = async command => {
      host.commands.push(command); if (host.postCommand) return host.postCommand(command); return {command_id:command.command_id,status:'applied'};
    };
    export const withdrawExecutionCommand = async commandId => ({withdrawn:true, command:{command_id:commandId,status:'rejected',rejection_code:'withdrawn'}});`,
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
globalThis.sessionStorage = undefined;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.WebSocket = { OPEN: 1 };
const { useChatSubmit, stopSession } = await import("../../../../components/chat/composer/submit/use-chat-submit.ts");
const { sendChatMessage } = await import("../../../../components/chat/composer/submit/send-chat-message.ts");
const { useSendQueue, queueFor, reconcileAfterSessionLoad, recheckRunState, releaseStopHold, stopPending } = await import("../../../../lib/chat/send/send-queue.ts");
const { pasteStore, placeholderToken } = await import("../../../../components/chat/composer/paste/paste-store.ts");
const pending = await import("../../../../lib/chat/send/pending-user-text.ts");

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
  host.sessions.rowsById[replyId] = { id: replyId, role: "assistant", status: "streaming", content: "partial" };
  host.sessions.messageOrder[sid] = [replyId];
  return task;
}
function composer(input, { runningMessageMode = "queue", ...overrides } = {}) {
  host.sessions.composerDrafts.A = input;
  const hook = useChatSubmit({
    bound: null, input, activeChatKey: "A", currentSessionId: "A", isRunning: true,
    noEnabledModels: false, promptNeedModel() {}, send: () => true,
    setComposerInputFor(owner, value) {
      host.draftWrites.push([owner, value]);
      host.sessions.composerDrafts[owner] = value;
      if (host.collectPastesOnClear && value === "") pasteStore.retainOnly(new Set());
    },
    setHistoryIndex() {}, slash: { runCommand: () => false, close() {} },
    pendingImages: [], pendingDocs: [], clearAttachmentsAfterSubmit() {},
    thinking: "medium", webSearchEnabled: false, fastEnabled: false, fastSupported: false,
    dispatchFunction: () => false,
    ...overrides,
  });
  // Enter steers, ⌘/Ctrl+Enter queues: the mode rides on each submit.
  return { ...hook, submit: () => hook.submit(runningMessageMode) };
}
function sendArgs(overrides = {}) {
  return { text: "hello", sessionId: "A", thinking: "medium", webSearchEnabled: false, ...overrides };
}

beforeEach(() => {
  for (const sid of ["A", "B", "local_new"]) {
    pending.clearPendingUserText(sid); pending.clearPendingFirstAck(sid);
    releaseStopHold(sid, false);
  }
  pasteStore.retainOnly(new Set());
  useSendQueue.setState({ queues: {} });
  Object.assign(host.runtime, { currentSessionId: "A", isRunning: false, _optimisticCancels: {}, _optimisticStops: {} });
  Object.assign(host, {
    snapshot: undefined, postCommand: undefined, frames: [], commands: [], notes: [], toasts: [], draftWrites: [], runWrites: [],
    globalRunWrites: [], welcomeWrites: [], collectPastesOnClear: false,
    expandMentions: async text => ({ text }),
  });
  host.sessions = {
    activeChatKey: "A", currentSessionId: "A", welcomeVisible: true,
    runningTasks: {}, rowsByView: {}, rowsById: {}, messageOrder: {}, composerDrafts: {}, pendingDecisions: [],
    pendingProjectsByChat: {}, composerSettingsBySession: {}, additionalWorkingDirsBySession: {},
    setRunningTaskFor(sid, task) {
      host.runWrites.push([sid, task]);
      if (task) this.runningTasks[sid] = task; else delete this.runningTasks[sid];
    },
    applyLocalChange(sid, change) { if (change.fields && this.rowsById[change.rowId]) Object.assign(this.rowsById[change.rowId], change.fields); },
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
test("attached queue captures files, supports editing and drains the edited payload", async () => {
  running();
  const doc = { id: "doc", filename: "before.txt", ext: "txt", sizeBytes: 3,
    content: "old", dataB64: "b2xk", order: 0 };
  let cleared = false;
  await composer("caption", {pendingDocs: [doc], clearAttachmentsAfterSubmit() { cleared = true; }}).submit();
  const row = queueFor("A")[0];
  assert.ok(row, "attachment message must enter the queue");
  assert.equal(cleared, true);
  assert.equal(row.docs[0].dataB64, "b2xk");
  assert.equal(useSendQueue.getState().beginEdit("A", row.id), true);
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  assert.equal(host.frames.length, 0, "editing holds the head");
  assert.equal(useSendQueue.getState().updateDraft("A", row.id, {text:"edited", images:[],
    docs:[{...doc, filename:"after.txt", content:"new", dataB64:"bmV3"}]}), true);
  useSendQueue.getState().drain("A");
  assert.equal(queueFor("A").length, 0);
  assert.match(host.frames[0].text, /edited/);
  assert.match(host.frames[0].text, /after.txt/);
  assert.doesNotMatch(host.frames[0].text, /before.txt/);
  assert.equal(host.frames[0].attachments[0].data, "bmV3");
});

test("attachment queue rejection restores exact edited files without a retry loop", async () => {
  running();
  await composer("", {pendingDocs:[{id:"d",filename:"file.txt",ext:"txt",sizeBytes:3,dataB64:"YWJj",content:"abc"}]}).submit();
  assert.equal(queueFor("A").length, 1);
  assert.equal(host.commands.length, 0);
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  pending.getPendingUserReject("A")?.();
  assert.equal(queueFor("A")[0].docs[0].dataB64, "YWJj");
  pending.getPendingUserReject("A")?.();
  assert.equal(queueFor("A").length, 1);
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  assert.equal(host.frames.length, 1, "rejection must not create an automatic retry loop");
  useSendQueue.getState().retryDraft("A", queueFor("A")[0].id);
  assert.equal(host.frames.length, 2);
});

test("queue editing and removal cannot mutate uncertain steering delivery", async () => {
  running(); await composer("keep").submit();
  const row = queueFor("A")[0];
  useSendQueue.getState().setSteering("A", row.id, {steerCommand:{command_id:"receipt"}});
  assert.equal(useSendQueue.getState().beginEdit("A", row.id), false);
  assert.equal(useSendQueue.getState().updateDraft("A", row.id, {text:"changed",images:[],docs:[]}), false);
  assert.equal(useSendQueue.getState().removeDraft("A", row.id), false);
  assert.equal(queueFor("A")[0].text, "keep");
});

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

test("a message sent while disconnected waits in the queue and goes out after the reconnect", async () => {
  host.socket.readyState = 3;
  await composer("send me later", { isRunning: false }).submit();
  assert.equal(host.frames.length, 0);
  assert.deepEqual(queueFor("A").map(row => row.text), ["send me later"]);
  assert.deepEqual(host.draftWrites, [["A", ""]]);
  host.socket.readyState = 1;
  // The reconnect asks for the run state; an idle reply drains the queue.
  reconcileAfterSessionLoad("A", false, null);
  await nextTurn();
  assert.deepEqual(host.frames.map(frame => frame.text), ["send me later"]);
  assert.equal(queueFor("A").length, 0);
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
      assert.equal(host.sessions.rowsById[replyId].status, "streaming");
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

test("pre-ACK stop names the send at once when its request id is known", () => {
  running("A", { provisional: true });
  pending.setPendingUserText("A", "hello", Date.now(), { requestId: "req-A" });
  const sent = [];
  stopSession("A", payload => { sent.push(payload); return true; });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].action, "execution.cancel");
  assert.equal(sent[0].session_id, "A");
  assert.equal(sent[0].client_request_id, "req-A");
  assert.equal("execution_id" in sent[0], false);
  // The ack sends no second cancel: it finds the stop already in force.
  assert.equal(host.runtime._optimisticStops.A.commandId, sent[0].command_id);
});

test("a Stop on a function run or retry before its answer names the run by its request id", () => {
  // A REST function run (or a Retry) is admitted with the id the page made;
  // the stop names it before the response names the execution (Codex
  // returns the turn id at once; the request id covers the gap).
  host.sessions.runningTasks.A = { session_id: "A", msg_id: "__optimistic_fn__:f:1", func_name: "f", client_request_id: "fn-req-A" };
  const sent = [];
  stopSession("A", payload => { sent.push(payload); return true; });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].client_request_id, "fn-req-A");
  assert.equal("execution_id" in sent[0], false);
  assert.equal(host.runtime._optimisticStops.A.commandId, sent[0].command_id);
  releaseStopHold("A", false);
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
  const {steerQueuedMessage}=await import('../../../../lib/chat/send/steer-message.ts');
  await steerQueuedMessage('A',queueFor('A')[0].id);
  assert.equal(timers.size,0);
  assert.equal(queueFor('A').length,0);
});


test("terminal rejection keeps a text queue entry until explicit retry", async () => {
  running(); await composer("keep plain draft").submit();
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  pending.getPendingUserReject("A")?.();
  assert.equal(queueFor("A")[0].text, "keep plain draft");
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  assert.equal(host.frames.length, 1);
});


test("image queue preview survives composer cleanup and original bytes survive drain", async () => {
  running();
  const image = {id:"image",sizeBytes:12,previewUrl:"blob:composer",attachment:{type:"image",media_type:"image/png",data:"c21hbGw=",original_data:"b3JpZ2luYWw=",original_media_type:"image/png",filename:"shot.png"}};
  await composer("image", {pendingImages:[image],clearAttachmentsAfterSubmit(){image.previewUrl=null;}}).submit();
  const row=queueFor("A")[0];
  assert.equal(row.images[0].previewUrl,"data:image/png;base64,b3JpZ2luYWw=");
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  assert.equal(host.frames[0].attachments[0].original_data,"b3JpZ2luYWw=");
});


test("new Agent conversations send the submitting draft configuration, including background panes", () => {
  host.sessions.composerSettingsBySession.local_new = {
    agentInvocation: {agentId: "research", trial: true, config: {system_prompt: "unsaved instruction", memory: {mode: "read_only"}}},
  };
  host.sessions.composerSettingsBySession.B = {agentInvocation: {agentId: "other"}};
  focus("B");
  sendChatMessage(sendArgs({sessionId: "local_new", background: true}));
  assert.equal(host.frames[0].agent_id, "research");
  assert.equal(host.frames[0].agent_trial, true);
  assert.equal(host.frames[0].agent_config.system_prompt, "unsaved instruction");
  assert.equal(host.sessions.composerSettingsBySession.local_new.agentInvocation.agentId, "research", "socket write is not an ACK");
});

test("saved Agent invocations retain only explicit per-conversation model overrides", () => {
  host.sessions.composerSettingsBySession.local_new = {
    agentInvocation: {agentId: "writer", model: {provider: "example", id: "fast"}, thinkingEffort: "low"},
  };
  sendChatMessage(sendArgs({sessionId: "local_new"}));
  assert.equal(host.frames[0].agent_id, "writer");
  assert.deepEqual(host.frames[0].agent_overrides, {model: {provider: "example", id: "fast"}, thinking_effort: "low"});
  assert.equal(host.frames[0].agent_config, undefined);
  assert.equal(host.frames[0].agent_trial, undefined);
});

test("stop rejects pending steer without automatically sending another turn", async () => {
  running();
  host.postCommand = command => {
    delete host.sessions.runningTasks.A;
    return {command_id:command.command_id,status:'rejected',rejection_code:'superseded_by_cancel'};
  };
  await composer('keep stopped instruction', {runningMessageMode:'steer'}).submit();
  await nextTurn();
  useSendQueue.getState().drain('A');
  assert.equal(host.frames.length,0);
  assert.equal(queueFor('A')[0].text,'keep stopped instruction');
  assert.equal(queueFor('A')[0].steerError,'cancelled');
  useSendQueue.getState().retryDraft('A',queueFor('A')[0].id);
  assert.equal(host.frames.length,1);
  assert.equal(host.frames[0].text,'keep stopped instruction');
});


test("Stop during steer snapshot retains instruction without a new chat", async () => {
  running();
  let release;
  host.snapshot = (eid, sid) => new Promise(resolve => { release = () => resolve({session_id:sid,execution_id:eid,status_version:7,status:'running',capabilities:{steer:true}}); });
  await composer('stop before admission', {runningMessageMode:'steer'}).submit();
  await nextTurn();
  stopSession('A', () => true);
  release(); await nextTurn();
  assert.equal(host.commands.length, 0);
  assert.equal(host.frames.length, 0);
  assert.equal(queueFor('A')[0].text, 'stop before admission');
  assert.equal(queueFor('A')[0].steerError, 'cancelled');
});

test("version retry never redirects a steer to a replacement execution", async () => {
  running();
  host.postCommand = command => {
    host.sessions.runningTasks.A = {session_id:'A',msg_id:'next',execution_id:'exec-next',status_version:1};
    return {command_id:command.command_id,status:'rejected',rejection_code:'stale_version'};
  };
  await composer('only for original turn', {runningMessageMode:'steer'}).submit();
  await nextTurn();
  assert.deepEqual(host.commands.map(c=>c.execution_id), ['exec-A']);
  assert.equal(queueFor('A')[0].steerError, 'ended');
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain('A');
  assert.equal(host.frames.length, 0);
});

test("Stop during version rejection retains draft without resubmission", async () => {
  running();
  host.postCommand = command => {
    stopSession('A', () => true);
    return {command_id:command.command_id,status:'rejected',rejection_code:'stale_version'};
  };
  await composer('do not restart after stop', {runningMessageMode:'steer'}).submit();
  await nextTurn();
  assert.equal(host.commands.length, 1);
  assert.equal(host.frames.length, 0);
  assert.equal(queueFor('A')[0].steerError, 'cancelled');
});

test("steering character limit agrees with server for non-BMP text", async () => {
  running();
  const text='😀'.repeat(3000);
  await composer(text, {runningMessageMode:'steer'}).submit();
  await nextTurn();
  assert.equal(host.commands[0]?.payload.message, text);
  assert.equal(queueFor('A').length, 0);
});


test("version retry refreshes only the original target and never loops indefinitely", async () => {
  running();
  let version=7;
  host.snapshot=(eid,sid)=>({session_id:sid,execution_id:eid,status_version:version++,status:'running',capabilities:{steer:true}});
  host.postCommand=command=>({command_id:command.command_id,status:'rejected',rejection_code:'stale_version'});
  await composer('bound retry', {runningMessageMode:'steer'}).submit();
  await nextTurn();
  assert.deepEqual(host.commands.map(c=>[c.execution_id,c.expected_version]), [['exec-A',7],['exec-A',8]]);
  assert.notEqual(host.commands[0].command_id,host.commands[1].command_id);
  assert.equal(queueFor('A')[0].text,'bound retry');
});

test("an attached send clears the composer files when the socket accepts it, not at ACK", async () => {
  const calls = [];
  const options = {
    isRunning: false,
    pendingDocs: [{ id: "doc", filename: "statement.pdf", ext: "pdf", sizeBytes: 3, dataB64: "YWJj", order: 0 }],
    clearAttachmentsAfterSubmit() { calls.push("clear"); },
    detachAttachmentsForSubmit(owner, ids) {
      calls.push(["detach", owner, ids.docIds]);
      return { release() { calls.push("release"); }, restore() { calls.push("restore"); } };
    },
  };
  await composer("see the file", options).submit();
  assert.equal(host.frames.length, 1);
  assert.deepEqual(calls, [["detach", "A", ["doc"]]], "the files leave with the text");
  pending.acknowledgePendingUserText("A");
  assert.deepEqual(calls, [["detach", "A", ["doc"]], "release"]);

  calls.length = 0;
  pending.clearPendingFirstAck("A");
  await composer("again", options).submit();
  pending.getPendingUserReject("A")?.();
  assert.deepEqual(calls, [["detach", "A", ["doc"]], "restore"], "a rejected turn gets its files back");
});

test("a clear before the previous send's ACK leaves the next queued message waiting", async () => {
  const sid = "local_new";
  useSendQueue.getState().enqueue(sid, { text: "first", thinking: "medium", webSearchEnabled: false });
  useSendQueue.getState().enqueue(sid, { text: "second", thinking: "medium", webSearchEnabled: false });
  useSendQueue.getState().drain(sid);
  assert.deepEqual(host.frames.map(frame => frame.text), ["first"]);
  // A session reload clears the placeholder task before chat_ack arrives.
  delete host.sessions.runningTasks[sid];
  useSendQueue.getState().drain(sid);
  assert.deepEqual(host.frames.map(frame => frame.text), ["first"]);
  assert.deepEqual(queueFor(sid).map(row => row.text), ["second"]);
  // ACK, then the first turn ends: the second message goes out.
  pending.acknowledgePendingUserText(sid); pending.clearPendingFirstAck(sid);
  useSendQueue.getState().drain(sid);
  assert.deepEqual(host.frames.map(frame => frame.text), ["first", "second"]);
  assert.equal(queueFor(sid).length, 0);
});

test("an active run reported without ids is asked again, so a finished turn cannot stay busy", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  reconcileAfterSessionLoad("A", true, null);
  assert.deepEqual(host.sessions.runningTasks.A, { session_id: "A", msg_id: "" });
  // The session store asks for this on every write of a slot without ids.
  recheckRunState("A");
  t.mock.timers.tick(1500);
  await nextTurn(); await nextTurn();
  assert.deepEqual(host.frames, [{ action: "get_run_state", session_id: "A" }]);
  // The reply names the session idle: the slot clears.
  reconcileAfterSessionLoad("A", false, null);
  assert.equal(host.sessions.runningTasks.A, undefined);
  // A reply that names the turn needs no second ask.
  host.frames.length = 0;
  reconcileAfterSessionLoad("B", true, { msg_id: "user-B", execution_id: "exec-B" });
  recheckRunState("B");
  t.mock.timers.tick(1500);
  await nextTurn(); await nextTurn();
  assert.deepEqual(host.frames, []);
});

// A Stop's drain hold asks the server on a timer; none may outlive the file.
after(() => { for (const sid of ["A", "B", "local_new"]) releaseStopHold(sid, false); });

/** Change the running slots the way a store write does, and tell subscribers. */
function setRunningTasks(patch) {
  const previous = { ...host.sessions, runningTasks: { ...host.sessions.runningTasks } };
  const next = { ...host.sessions.runningTasks, ...patch };
  for (const [sid, task] of Object.entries(patch)) if (!task) delete next[sid];
  host.sessions.runningTasks = next;
  for (const listener of [...host.storeListeners]) listener(host.sessions, previous);
}

function slashProbe(outcome = "ran") {
  const calls = [];
  return { calls, slash: { close() {}, runCommand(text, submitter) {
    calls.push({ text, busy: submitter.busy });
    return typeof outcome === "function" ? outcome(text, submitter) : outcome;
  } } };
}

test("a slash command with arguments typed during a run runs as a command, not as queued text", async () => {
  running();
  const probe = slashProbe();
  await composer("/compact 5000", { slash: probe.slash, runningMessageMode: "steer" }).submit();
  assert.deepEqual(probe.calls, [{ text: "/compact 5000", busy: true }]);
  assert.equal(queueFor("A").length, 0);
  assert.equal(host.commands.length, 0);
  assert.deepEqual(host.draftWrites, [["A", ""]]);
});

test("a slash command while a request waits is a command, not the reply", async () => {
  host.sessions.pendingDecisions = [{ id: "d1", sessionId: "A", executionId: "exec-A", expectedVersion: 3 }];
  const probe = slashProbe();
  await composer("/branch release", { slash: probe.slash, isRunning: false }).submit();
  assert.deepEqual(probe.calls, [{ text: "/branch release", busy: true }]);
  assert.equal(host.commands.length, 0, "no wait answer was posted");
});

test("a command that could not run keeps the draft", async () => {
  const probe = slashProbe("failed");
  await composer("/compact", { slash: probe.slash, isRunning: false }).submit();
  assert.equal(probe.calls.length, 1);
  assert.deepEqual(host.draftWrites, []);
  assert.equal(host.frames.length, 0);
});

test("a chat-type command takes the normal send path, and waits in the queue during a run", async () => {
  host.sessions.composerSettingsBySession.A = { permission_mode: "plan" };
  const sendTask = (text, submitter) => (submitter.sendChat("/task go") ? "ran" : "failed");
  await composer("/task go", { slash: slashProbe(sendTask).slash, isRunning: false }).submit();
  assert.equal(host.frames[0].action, "chat");
  assert.equal(host.frames[0].text, "/task go");
  assert.equal(host.frames[0].permission_mode, "plan", "the chat's own settings ride along");
  assert.ok(host.sessions.messageOrder.A?.length, "it gets the optimistic bubble");

  host.frames.length = 0;
  running();
  await composer("/task go", { slash: slashProbe(sendTask).slash }).submit();
  assert.equal(host.frames.length, 0);
  assert.deepEqual(queueFor("A").map(row => row.text), ["/task go"]);
});

const runLocal = (text, submitter) => (submitter.sendCommand(text) ? "ran" : "failed");

test("a backend command from a draft is the draft's first message, with its id and settings", async () => {
  focus("local_new");
  host.sessions.composerSettingsBySession.local_new = { permission_mode: "plan", agentInvocation: { agentId: "writer" } };
  host.sessions.pendingProjectsByChat.local_new = "proj-1";
  host.sessions.additionalWorkingDirsBySession.local_new = ["/w/extra"];
  host.sessions.composerDrafts.local_new = "/goal ship it";
  const raw = [];
  await composer("/goal ship it", {
    activeChatKey: "local_new", currentSessionId: null, isRunning: false,
    send: frame => (raw.push(frame), true), slash: slashProbe(runLocal).slash,
  }).submit();
  assert.deepEqual(raw, [], "never a bare frame the server would give a conversation of its own");
  const frame = host.frames[0];
  assert.equal(frame.action, "chat");
  assert.equal(frame.session_id, "local_new");
  assert.equal(frame.text, "/goal ship it");
  assert.equal(frame.permission_mode, "plan");
  assert.equal(frame.agent_id, "writer");
  assert.equal(frame.project_id, "proj-1");
  assert.deepEqual(frame.additional_working_dirs, ["/w/extra"]);
  assert.ok(frame.client_request_id, "the ack and the reply name this send");
  // No bubble yet: the ack draws one for a command that goes on as a turn
  // (/goal <text>); one that only answers (/goal status) leaves its reply.
  assert.deepEqual(host.sessions.messageOrder.local_new ?? [], []);
  assert.equal(pending.getPendingUserText("local_new"), "/goal ship it", "the ack pairs with this send");
  assert.equal(pending.hasPendingFirstAck("local_new"), true);
  assert.deepEqual(host.draftWrites.at(-1), ["local_new", ""]);
});

test("a backend command in a split pane goes to that pane's conversation", async () => {
  await composer("/goal status", {
    bound: "B", activeChatKey: "B", currentSessionId: "B", isRunning: false, slash: slashProbe(runLocal).slash,
  }).submit();
  assert.equal(host.frames[0].session_id, "B");
  assert.equal(host.frames[0].text, "/goal status");
  assert.deepEqual(host.welcomeWrites, [], "the focused chat's shell is not touched");
});

test("a backend command during a run goes at once, beside the turn", async () => {
  running();
  const raw = [];
  await composer("/goal status", { send: frame => (raw.push(frame), true), slash: slashProbe(runLocal).slash }).submit();
  assert.deepEqual(raw, [{ action: "chat", session_id: "A", text: "/goal status" }]);
  assert.equal(queueFor("A").length, 0);
  assert.equal(host.frames.length, 0);
});

test("a composer with no chat key sends the settings kept under __new__", () => {
  host.sessions.composerSettingsBySession.__new__ = { permission_mode: "acceptEdits", agentInvocation: { agentId: "writer" } };
  sendChatMessage(sendArgs({ sessionId: null }));
  assert.equal(host.frames[0].permission_mode, "acceptEdits");
  assert.equal(host.frames[0].agent_id, "writer");
});

test("a parked row does not hold back the messages queued behind it", () => {
  const queue = useSendQueue.getState();
  const parked = queue.enqueue("A", { text: "not sent", thinking: "medium", webSearchEnabled: false, background: false });
  queue.setSteering("A", parked, { steerError: "ended" });
  queue.enqueue("A", { text: "next", thinking: "medium", webSearchEnabled: false, background: false });
  queue.drain("A");
  assert.deepEqual(host.frames.map(frame => frame.text), ["next"]);
  assert.deepEqual(queueFor("A").map(row => row.text), ["not sent"]);
});

test("a steer whose turn ended during the snapshot read goes out as the next turn", async () => {
  running();
  host.snapshot = (eid, sid) => {
    delete host.sessions.runningTasks.A;
    return { session_id: sid, execution_id: eid, status_version: 7, status: "running", capabilities: { steer: true } };
  };
  await composer("late instruction", { runningMessageMode: "steer" }).submit();
  await nextTurn(); await nextTurn();
  assert.equal(host.commands.length, 0);
  assert.deepEqual(host.frames.map(frame => frame.text), ["late instruction"]);
  assert.equal(queueFor("A").length, 0);
});

test("after Stop the queue waits until the server has released the turn", async () => {
  running();
  await composer("after stop").submit();
  stopSession("A", () => true);
  assert.equal(host.sessions.runningTasks.A, undefined, "the composer is free at once");
  await nextTurn(); await nextTurn();
  assert.equal(host.frames.length, 0);
  assert.equal(stopPending("A"), true);
  // The stopped run is still unwinding: the slot stays free, nothing is sent.
  reconcileAfterSessionLoad("A", true, { msg_id: "user-A", execution_id: "exec-A" });
  assert.equal(host.sessions.runningTasks.A, undefined);
  await nextTurn();
  assert.equal(host.frames.length, 0);
  reconcileAfterSessionLoad("A", false, null);
  await nextTurn(); await nextTurn();
  assert.equal(stopPending("A"), false);
  assert.deepEqual(host.frames.map(frame => frame.text), ["after stop"]);
});

test("a Stop on a turn known by id but not by version cancels at once", async () => {
  // A reconnect's run state handed over the ids alone. A stop names its
  // execution, so it needs no version and goes out without asking.
  host.sessions.runningTasks.A = { session_id: "A", msg_id: "user-A", execution_id: "exec-A" };
  host.sessions.rowsById["exec-A"] = { id: "exec-A", role: "assistant", status: "streaming", content: "partial" };
  host.sessions.messageOrder.A = ["exec-A"];
  const frames = [];
  stopSession("A", (frame) => { frames.push(frame); return true; });
  assert.equal(frames.length, 1);
  assert.deepEqual([frames[0].action, frames[0].execution_id], ["execution.cancel", "exec-A"]);
  assert.equal(host.frames.some(frame => frame.action === "get_run_state"), false);
  assert.equal(host.sessions.runningTasks.A, undefined);
  assert.equal(host.runtime._optimisticCancels[frames[0].command_id].sessionId, "A");
  releaseStopHold("A", false);
});

test("a steer sent before the turn had an execution id goes out once the id arrives", async () => {
  running("A", { provisional: true });
  await composer("early instruction", { runningMessageMode: "steer" }).submit();
  await nextTurn();
  assert.equal(queueFor("A")[0].steerError, "retry");
  assert.equal(host.commands.length, 0);
  setRunningTasks({ A: { session_id: "A", msg_id: "user-A", execution_id: "exec-A", status_version: 7 } });
  await nextTurn(); await nextTurn();
  assert.deepEqual(host.commands.map(command => [command.execution_id, command.payload.message]), [["exec-A", "early instruction"]]);
  assert.equal(queueFor("A").length, 0);
});

test("a queued message resolves its @ mentions and keeps its pasted span collapsed", async () => {
  running();
  const pasted = "row\n".repeat(300);
  host.expandMentions = async text => ({ text: text.replace("@notes.md", "[attachment: notes.md (md, 1 KB) @ /work/notes.md]") });
  await composer(`see @notes.md and ${placeholderToken(pasteStore.add(pasted))}`).submit();
  const row = queueFor("A")[0];
  assert.equal(row.text, `see [attachment: notes.md (md, 1 KB) @ /work/notes.md] and ${pasted}`);
  assert.equal(row.textElements.length, 1);
  delete host.sessions.runningTasks.A;
  useSendQueue.getState().drain("A");
  assert.equal(host.frames[0].text, row.text);
  assert.deepEqual(host.frames[0].text_elements, row.textElements);
});

test("a reply that was not delivered comes back only into an empty box, as typed", async () => {
  host.sessions.pendingDecisions = [{ id: "d2", sessionId: "A", executionId: "exec-A" }];
  const token = placeholderToken(pasteStore.add("long\n".repeat(300)));
  const typedMeanwhile = { setComposerInputFor(owner, value) {
    host.draftWrites.push([owner, value]);
    host.sessions.composerDrafts[owner] = value === "" ? "typed meanwhile" : value;
  } };
  await composer("do this instead", { isRunning: false, ...typedMeanwhile }).submit();
  assert.equal(host.sessions.composerDrafts.A, "typed meanwhile");
  host.draftWrites.length = 0;
  await composer(`use ${token}`, { isRunning: false }).submit();
  assert.deepEqual(host.draftWrites, [["A", ""], ["A", `use ${token}`]]);
});

test("each chat frame carries its own client_request_id, kept with the pending turn", () => {
  sendChatMessage(sendArgs({ text: "first" }));
  const first = host.frames[0].client_request_id;
  assert.match(first, /^[0-9a-f-]{36}$/);
  assert.equal(pending.getPendingUserRequestId("A"), first);
  sendChatMessage(sendArgs({ sessionId: "B", text: "second" }));
  assert.notEqual(host.frames[1].client_request_id, first);
  pending.rejectPendingUserText("A");
  assert.equal(pending.getPendingUserRequestId("A"), undefined);
});
