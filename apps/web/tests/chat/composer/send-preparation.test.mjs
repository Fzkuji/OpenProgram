/**
 * Mount the real useChatSubmit hook with React createRoot and the real sender,
 * queue, steer, pending-text and paste-store modules. Session/UI ports,
 * Zustand storage and transport are controlled fixtures. These tests do not
 * verify native browser scrolling or backend ACK delivery.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks, createRequire } from "node:module";
const require = createRequire(new URL("../../../package.json", import.meta.url));
const { act, createElement } = require("react");
const { createRoot } = require("react-dom/client");
const { parseHTML } = require("linkedom");
import { after, beforeEach, test } from "node:test";
import { fileURLToPath } from "node:url";
const originalGlobals = new Map(["window", "document", "IS_REACT_ACT_ENVIRONMENT", "sessionStorage", "localStorage", "WebSocket", "__submitReliability"].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
after(() => {
    moduleHooks.deregister();
    for (const [key, descriptor] of originalGlobals) {
        if (descriptor)
            Object.defineProperty(globalThis, key, descriptor);
        else
            delete globalThis[key];
    }
});
const webRoot = new URL("../../../", import.meta.url);
const host = globalThis.__submitReliability = { runtime: {} };
const mocks = {
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
    "@/lib/session-store": "export const useSessionStore = { getState: () => host.sessions }; export const selectViewRows = () => (state) => state.rowsById;",
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
const moduleHooks = registerHooks({
    resolve(specifier, context, nextResolve) {
        if (Object.hasOwn(mocks, specifier)) {
            return {
                url: "data:text/javascript," + encodeURIComponent("const host = globalThis.__submitReliability;\n" + mocks[specifier]),
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
                if (existsSync(fileURLToPath(url)))
                    return { url, shortCircuit: true };
            }
        }
        return nextResolve(specifier, context);
    },
});
globalThis.window = {};
globalThis.sessionStorage = undefined;
globalThis.localStorage = { getItem: () => null, setItem() { } };
globalThis.WebSocket = { OPEN: 1 };
const { useChatSubmit } = await import("../../../components/chat/composer/submit/use-chat-submit.ts");
const { useSendQueue, queueFor, releaseStopHold } = await import("../../../lib/chat/send/send-queue.ts");
const { pasteStore } = await import("../../../components/chat/composer/paste/paste-store.ts");
const pending = await import("../../../lib/chat/send/pending-user-text.ts");
function focus(sid) {
    host.sessions.activeChatKey = sid;
    host.sessions.currentSessionId = sid?.startsWith("local_") ? null : sid;
    host.runtime.currentSessionId = host.sessions.currentSessionId;
}
function composer(input, { runningMessageMode = "queue", ...overrides } = {}) {
    host.sessions.composerDrafts[overrides.activeChatKey ?? "A"] = input;
    const hook = useChatSubmit({
        bound: null, input, activeChatKey: "A", currentSessionId: "A", isRunning: true,
        noEnabledModels: false, promptNeedModel() { }, send: () => true,
        setComposerInputFor(owner, value) {
            host.draftWrites.push([owner, value]);
            host.sessions.composerDrafts[owner] = value;
            if (host.collectPastesOnClear && value === "")
                pasteStore.retainOnly(new Set());
        },
        setHistoryIndex() { }, slash: { runCommand: () => false, close() { } },
        pendingImages: [], pendingDocs: [], clearAttachmentsAfterSubmit() { },
        thinking: "medium", webSearchEnabled: false, fastEnabled: false, fastSupported: false,
        dispatchFunction: () => false,
        ...overrides,
    });
    // Enter steers, ⌘/Ctrl+Enter queues: the mode rides on each submit.
    return { ...hook, submit: () => hook.submit(runningMessageMode) };
}
beforeEach(() => {
    for (const sid of ["A", "B", "local_new"]) {
        pending.clearPendingUserText(sid);
        pending.clearPendingFirstAck(sid);
        releaseStopHold(sid, false);
    }
    pasteStore.retainOnly(new Set());
    useSendQueue.setState({ queues: {} });
    Object.assign(host.runtime, { currentSessionId: "A", isRunning: false, _optimisticCancels: {}, _optimisticStops: {} });
    Object.assign(host, {
        snapshot: undefined, postCommand: undefined, frames: [], commands: [], notes: [], toasts: [], draftWrites: [], runWrites: [],
        globalRunWrites: [], welcomeWrites: [], collectPastesOnClear: false,
        expandMentions: async (text) => ({ text }),
    });
    host.sessions = {
        activeChatKey: "A", currentSessionId: "A", welcomeVisible: true,
        runningTasks: {}, rowsByView: {}, rowsById: {}, messageOrder: {}, composerDrafts: {}, pendingDecisions: [],
        pendingProjectsByChat: {}, composerSettingsBySession: {}, additionalWorkingDirsBySession: {},
        setRunningTaskFor(sid, task) {
            host.runWrites.push([sid, task]);
            if (task)
                this.runningTasks[sid] = task;
            else
                delete this.runningTasks[sid];
        },
    };
    host.socket = { readyState: 1, send: wire => host.frames.push(JSON.parse(wire)) };
});
async function mounted(specs) {
    const parsed = parseHTML('<html><body><div id="root"></div></body></html>');
    Object.assign(globalThis, { window: parsed.window, document: parsed.document, IS_REACT_ACT_ENVIRONMENT: true });
    const views = [];
    const root = createRoot(document.getElementById("root"));
    function Probe({ i, s }) { views[i] = composer(s.text, { isRunning: false, activeChatKey: s.owner, currentSessionId: s.owner, bound: s.bound ?? null, ...s.overrides }); return null; }
    const render = () => root.render(createElement("div", null, ...specs.map((s, i) => createElement(Probe, { key: i, i, s }))));
    await act(render);
    return { views, async update(next) { specs = next; await act(render); }, async close() { await act(() => root.unmount()); } };
}
function deferMentions() { const pending = []; host.expandMentions = text => new Promise((resolve, reject) => pending.push({ text, resolve: () => resolve({ text }), reject })); return pending; }
test("same hook and focused/bound composers share one pending owner intent", async () => {
    const d = deferMentions();
    const v = await mounted([{ owner: "A", text: "same" }, { owner: "A", text: "same", bound: "A" }]);
    try {
        const a = v.views[0].submit();
        const repeated = v.views[0].submit();
        const peer = v.views[1].submit();
        assert.equal(d.length, 1);
        d[0].resolve();
        await Promise.all([a, repeated, peer]);
        assert.equal(host.frames.length, 1);
        assert.equal(host.sessions.composerDrafts.A, "", "unchanged accepted intent clears its body");
    }
    finally {
        await v.close();
    }
});
test("switch from preparing A to B permits independent captured sends", async () => {
    const d = deferMentions();
    const v = await mounted([{ owner: "A", text: "a" }]);
    try {
        const a = v.views[0].submit();
        focus("B");
        await v.update([{ owner: "B", text: "b" }]);
        const b = v.views[0].submit();
        assert.equal(d.length, 2);
        d[1].resolve();
        await b;
        d[0].resolve();
        await a;
        assert.deepEqual(host.frames.map(x => [x.session_id, x.text]), [["B", "b"], ["A", "a"]]);
    }
    finally {
        await v.close();
    }
});
test("older preparation does not clear edited owner draft", async () => {
    const d = deferMentions();
    const v = await mounted([{ owner: "A", text: "old" }]);
    try {
        const a = v.views[0].submit();
        await v.update([{ owner: "A", text: "new draft" }]);
        d[0].resolve();
        await a;
        assert.equal(host.sessions.composerDrafts.A, "new draft");
        assert.equal(host.frames[0].text, "old");
    }
    finally {
        await v.close();
    }
});
test("mention rejection falls back to original text and later explicit send remains possible", async () => {
    const d = deferMentions();
    const v = await mounted([{ owner: "A", text: "first @file" }]);
    try {
        const a = v.views[0].submit();
        d[0].reject(new Error("resolve failed"));
        await a;
        assert.equal(host.frames.length, 1);
        assert.equal(host.frames[0].text, "first @file");
        await v.update([{ owner: "A", text: "second @file" }]);
        const b = v.views[0].submit();
        d[1].resolve();
        await b;
        assert.equal(host.frames.length, 2);
        assert.equal(host.frames[1].text, "second @file");
    }
    finally {
        await v.close();
    }
});
test("different draft submitted during preparation is observably distinct", async () => {
    const d = deferMentions();
    const v = await mounted([{ owner: "A", text: "old" }]);
    try {
        const a = v.views[0].submit();
        await v.update([{ owner: "A", text: "new intent" }]);
        const b = v.views[0].submit();
        d.forEach(x => x.resolve());
        await Promise.all([a, b]);
        assert.deepEqual(host.frames.map(x => x.text), ["old"]);
        assert.equal(host.sessions.composerDrafts.A, "new intent");
        assert.ok(host.toasts[0][0].includes("was not sent"));
    }
    finally {
        await v.close();
    }
});
test("actual mention body timeout sends fallback without clearing an edited draft", async () => {
    const { expandAtMentions } = await import("../../../components/chat/composer/attach/mention/at-mention.ts");
    const originalFetch = globalThis.fetch;
    const originalTimeout = AbortSignal.timeout;
    let controller;
    let bodyStarted;
    const ready = new Promise(resolve => { bodyStarted = resolve; });
    AbortSignal.timeout = milliseconds => {
        assert.equal(milliseconds, 15000);
        controller = new AbortController();
        return controller.signal;
    };
    globalThis.fetch = async (_url, { signal }) => ({
        ok: true,
        json: () => new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
            bodyStarted();
        }),
    });
    host.expandMentions = text => expandAtMentions(text, null);
    const view = await mounted([{ owner: "A", text: "inspect @frozen.txt" }]);
    let submission;
    try {
        submission = view.views[0].submit();
        await Promise.race([
            ready,
            submission.then(() => { throw new Error("submission settled before mention body started"); }),
        ]);
        await view.update([{ owner: "A", text: "edited draft" }]);
        controller.abort();
        await submission;
        assert.equal(host.frames.length, 1);
        assert.equal(host.frames[0].text, "inspect @frozen.txt");
        assert.equal(host.sessions.composerDrafts.A, "edited draft");
        host.expandMentions = async (text) => ({ text });
        await view.views[0].submit();
        assert.equal(host.frames.length, 2);
    }
    finally {
        controller?.abort();
        await submission;
        await view.close();
        globalThis.fetch = originalFetch;
        AbortSignal.timeout = originalTimeout;
    }
});

test("closed socket queues the draft for the reconnect instead of writing", async () => {
    const view = await mounted([{ owner: "A", text: "send after reconnect" }]);
    const socket = host.socket;
    host.socket = { readyState: 3 };
    try {
        await view.views[0].submit();
        assert.equal(host.frames.length, 0);
        assert.deepEqual(queueFor("A").map(row => row.text), ["send after reconnect"]);
        assert.equal(host.sessions.composerDrafts.A, "");
    } finally {
        host.socket = socket;
        await view.close();
    }
});

for (const failure of ["socket write exception"]) {
    test(`${failure} retains the draft and releases preparation for explicit retry`, async (t) => {
        const view = await mounted([{ owner: "A", text: "retry this draft" }]);
        const socket = host.socket;
        let lookups = 0;
        host.expandMentions = async text => { lookups += 1; return { text }; };
        if (failure === "closed socket") {
            host.socket = { readyState: 3 };
        } else {
            t.mock.method(console, "error", () => {});
            host.socket = { readyState: 1, send() { throw new Error("controlled write failure"); } };
        }
        try {
            await view.views[0].submit();
            assert.equal(host.frames.length, 0);
            assert.equal(host.sessions.composerDrafts.A, "retry this draft");
            assert.equal(host.draftWrites.length, 0);
            assert.deepEqual(host.sessions.messageOrder.A ?? [], []);
            host.socket = socket;
            await view.views[0].submit();
            assert.equal(lookups, 2);
            assert.equal(host.frames.length, 1);
            assert.equal(host.frames[0].text, "retry this draft");
            assert.equal(host.sessions.composerDrafts.A, "");
        } finally {
            host.socket = socket;
            await view.close();
        }
    });
}

test("escaped transport lookup exception retains draft and releases exact reservation", async () => {
    const view = await mounted([{ owner: "A", text: "retry after exception" }]);
    const socketDescriptor = Object.getOwnPropertyDescriptor(host, "socket");
    let lookups = 0;
    host.expandMentions = async text => { lookups += 1; return { text }; };
    Object.defineProperty(host, "socket", {
        configurable: true,
        get() { throw new Error("controlled socket lookup failure"); },
    });
    try {
        await assert.rejects(view.views[0].submit(), /controlled socket lookup failure/);
        assert.equal(host.frames.length, 0);
        assert.equal(host.sessions.composerDrafts.A, "retry after exception");
        assert.equal(host.draftWrites.length, 0);
        Object.defineProperty(host, "socket", socketDescriptor);
        await view.views[0].submit();
        // The connection check comes before mention resolution now.
        assert.equal(lookups, 1);
        assert.equal(host.frames.length, 1);
        assert.equal(host.frames[0].text, "retry after exception");
        assert.equal(host.sessions.composerDrafts.A, "");
    } finally {
        Object.defineProperty(host, "socket", socketDescriptor);
        await view.close();
    }
});

test("changed attachment intent keeps its unsent caption when older preparation settles", async () => {
    const d = deferMentions();
    const v = await mounted([{ owner: "A", text: "inspect @file.txt" }]);
    const added = { id: "new-doc", order: 1, filename: "new.txt", sourcePath: "/tmp/new.txt", ext: "txt", sizeBytes: 3, dataB64: "YWJj", content: "abc" };
    try {
        const older = v.views[0].submit();
        await v.update([{ owner: "A", text: "inspect @file.txt", overrides: { pendingDocs: [added] } }]);
        await v.views[0].submit();
        assert.equal(d.length, 1, "changed attachment intent is explicitly not submitted during old preparation");
        assert.match(host.toasts.at(-1)[0], /This draft was not sent/);
        assert.equal(host.sessions.composerDrafts.A, "inspect @file.txt");
        d[0].resolve();
        await older;
        assert.equal(host.frames.length, 1);
        assert.equal(host.frames[0].text, "inspect @file.txt");
        assert.equal(host.frames[0].attachments, undefined);
        assert.equal(host.sessions.composerDrafts.A, "inspect @file.txt", "caption of the explicitly unsent changed attachment intent must remain");
    } finally {
        for (const x of d) x.resolve();
        await v.close();
    }
});

for (const peer of [false, true]) {
    test(`changed settings intent preserves same body (${peer ? "peer" : "same"} composer)`, async () => {
        const d = deferMentions();
        const caption = "inspect @file.txt";
        const specs = [{ owner: "A", text: caption }];
        if (peer) specs.push({ owner: "A", text: caption, bound: "A" });
        const v = await mounted(specs);
        try {
            const older = v.views[0].submit();
            specs[peer ? 1 : 0] = { ...specs[peer ? 1 : 0], overrides: { thinking: "high" } };
            await v.update(specs);
            await v.views[peer ? 1 : 0].submit();
            assert.equal(d.length, 1);
            assert.match(host.toasts.at(-1)[0], /This draft was not sent/);
            d[0].resolve();
            await older;
            assert.equal(host.frames.length, 1);
            assert.equal(host.frames[0].text, caption);
            assert.equal(host.sessions.composerDrafts.A, caption);
        } finally {
            for (const x of d) x.resolve();
            await v.close();
        }
    });
}
