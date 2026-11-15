/**
 * Feeds a recorded server timeline through the production WebSocket hook,
 * reducers and row components, then opens the same chat from its stored
 * transcript and reports where the two views differ.
 *
 * Recordings come from tests/support/transcript/record_chats.py. Nothing here
 * edits a frame: the only value added to one is the request id of the read it
 * answers, which only the client knows.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { installLoader } from "./load.mjs";
import { installDom, render } from "./dom.mjs";

const SESSION_ID = "transcript-recording";
const READ_FIELDS = ["history_before", "history_after", "history_around", "history_latest"];

/** Row fields that describe this browser's moment, not the transcript. */
const CLIENT_ONLY_FIELDS = new Set(["timestamp", "streamSeq", "rawType", "autoPreviewFiles"]);

/** Fields a read adds and live frames do not carry until the publisher does. */
const READ_ONLY_FIELDS = new Set(["rowKind", "origin", "clientRequestId", "continues", "coveredBy", "blockId"]);
const READ_ONLY_BLOCK_FIELDS = new Set(["id", "index"]);

export function loadRecordings(directory) {
  return readdirSync(directory).filter((name) => name.endsWith(".json")).sort()
    .map((name) => JSON.parse(readFileSync(join(directory, name), "utf8")));
}

let booted;
function boot() {
  booted ??= (async () => {
    installLoader({
      replace: {
        "/components/chat/messages/turn-files/turn-files-chips.tsx": "export const TurnFilesChips = () => null;",
      },
    });
    installDom({ pathname: `/s/${SESSION_ID}` });
    const client = { sockets: [], sent: [], answer: null, executions: new Map() };
    class ReplaySocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      listeners = new Set();
      constructor() { client.sockets.push(this); }
      addEventListener(type, listener) { if (type === "message") this.listeners.add(listener); }
      removeEventListener(type, listener) { if (type === "message") this.listeners.delete(listener); }
      send(raw) {
        if (raw === "ping") return;
        const message = JSON.parse(raw);
        client.sent.push({ socket: this, message, answered: false });
        // A skipped event number makes the client ask for the execution's state.
        if (message.action === "execution.replay" && client.executions.has(message.execution_id)) {
          const { execution, event_cursor } = client.executions.get(message.execution_id);
          setTimeout(() => deliver(this, { type: "execution.replay", execution_id: message.execution_id, data: { snapshot: execution, event_cursor } }), 0);
          return;
        }
        if (client.answer && message.action === "load_session" && READ_FIELDS.some((field) => field in message)) {
          const frames = client.answer;
          client.answer = null;
          setTimeout(() => deliverRead(this, frames, message.request_id), 0);
          client.sent.at(-1).answered = true;
        }
      }
      close() { this.readyState = 3; this.onclose?.(); }
    }
    globalThis.WebSocket = ReplaySocket;
    window.WebSocket = ReplaySocket;
    globalThis.fetch = async () => ({ status: 200, ok: true, json: async () => ({}) });
    const { createElement } = await import("react");
    const { useWS } = await import("../../lib/net/ws/use-ws.ts");
    const { appendLocalUserTurn } = await import("../../lib/net/ws/chat-stream.ts");
    const { flushTextFrames } = await import("../../lib/net/ws/text-frames.ts");
    const { setPendingUserText } = await import("../../lib/chat/send/pending-user-text.ts");
    const { requestSessionLoad } = await import("../../lib/runtime-bridge/session/session-load.ts");
    const { loadSessionHistoryWindow } = await import("../../lib/runtime-bridge/session/session-history-loader.ts");
    const { runtimeState } = await import("../../lib/runtime-bridge/state.ts");
    const { useSessionStore, selectRow } = await import("../../lib/session-store/index.ts");
    const { useSessionHistory, HISTORY_PROTOCOL } = await import("../../lib/chat/history/session-history.ts");
    const { useSendQueue } = await import("../../lib/chat/send/send-queue.ts");
    const { dropSessionStore } = await import("../../lib/session-store/session-scope-registry.ts");
    const { clearSessionByMsgId } = await import("../../lib/net/ws/chat-stream.ts");
    const { clearPendingUserText, clearPendingFirstAck } = await import("../../lib/chat/send/pending-user-text.ts");
    const { resetRunSeqForTests } = await import("../../lib/chat/decisions/run-seq.ts");
    const { MessageRow } = await import("../../components/chat/messages/list/rows/message-rows.tsx");
    return {
      HISTORY_PROTOCOL, client, createElement, useWS, appendLocalUserTurn, flushTextFrames, selectRow, setPendingUserText,
      requestSessionLoad, loadSessionHistoryWindow, runtimeState, useSessionStore, MessageRow,
      stores: [useSessionStore, useSessionHistory, useSendQueue],
      forget() {
        dropSessionStore(SESSION_ID);
        clearSessionByMsgId();
        clearPendingUserText(SESSION_ID);
        clearPendingFirstAck(SESSION_ID);
        resetRunSeqForTests();
      },
    };
  })();
  return booted;
}

async function flush() {
  const { act } = await import("react");
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

function deliver(socket, frame) {
  if (frame.type === "execution.updated" && frame.execution) {
    booted?.then((app) => app.client.executions.set(frame.execution.execution_id, { execution: frame.execution, event_cursor: frame.event_cursor }));
  }
  const event = { data: JSON.stringify(frame) };
  socket.onmessage?.(event);
  for (const listener of [...socket.listeners]) listener(event);
}

function deliverRead(socket, frames, requestId) {
  for (const frame of frames) {
    deliver(socket, frame.type === "session_loaded" || frame.type === "session_history_page"
      ? { ...frame, data: { ...frame.data, request_id: requestId } } : frame);
  }
}

async function settle(app) {
  app.flushTextFrames();
  await flush();
  app.flushTextFrames();
  await flush();
}

async function mount(app) {
  const { createElement, useWS } = app;
  function Host() { useWS(); return null; }
  const before = app.client.sockets.length;
  const host = await render(createElement(Host));
  const socket = app.client.sockets[before];
  app.runtimeState.currentSessionId = SESSION_ID;
  socket.onopen?.();
  await settle(app);
  return { host, socket };
}

/** Every view starts as a new page: nothing an earlier view left shows through. */
function resetClient(app) {
  // Emptying the session store also releases its retained history windows.
  for (const store of app.stores) store.setState(store.getInitialState(), true);
  app.useSessionStore.setState({ currentSessionId: SESSION_ID });
  app.forget();
  app.runtimeState.conversations = {};
  app.runtimeState.pendingResponses = {};
  app.runtimeState.isRunning = false;
  app.runtimeState.isPaused = false;
  window.localStorage.clear();
  window.sessionStorage.clear();
  app.client.sent.length = 0;
  app.client.executions.clear();
  app.client.answer = null;
}

async function waitFor(predicate, what) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const value = predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** The transcript read this socket has out: the newest one nothing answered. */
function unansweredRead(app, socket) {
  return app.client.sent.findLast((entry) => entry.socket === socket && !entry.answered
    && entry.message.action === "load_session" && !READ_FIELDS.some((field) => field in entry.message));
}

async function answerRead(app, socket, frames) {
  let read = unansweredRead(app, socket);
  if (!read) {
    app.requestSessionLoad({ action: "load_session", session_id: SESSION_ID, history_version: app.HISTORY_PROTOCOL }, true);
    read = unansweredRead(app, socket);
  }
  if (!read) throw new Error("the client did not ask for a transcript read");
  read.answered = true;
  deliverRead(socket, frames, read.message.request_id);
  await settle(app);
}

async function captureView(app) {
  await settle(app);
  const state = app.useSessionStore.getState();
  const ids = [...(state.messageOrder[SESSION_ID] ?? [])];
  const rows = Object.fromEntries(ids.map((id) => [id, structuredClone(app.selectRow(SESSION_ID, id)(state))]));
  const dom = {};
  for (const id of ids) {
    const view = await render(app.createElement(app.MessageRow, { id, sessionIdOverride: SESSION_ID }));
    dom[id] = normalizeMarkup(view.container.innerHTML);
    await view.unmount();
  }
  return { ids, rows, dom };
}

/** What each row's execution strips draw once opened, in order: a card's id
 *  for a sub-agent step, "function" for any other step. */
async function captureSteps(app, ids) {
  const steps = {};
  for (const id of ids) {
    const view = await render(app.createElement(app.MessageRow, { id, sessionIdOverride: SESSION_ID }));
    const { act } = await import("react");
    await act(async () => { for (const toggle of view.container.querySelectorAll(".tl-toggle")) toggle.click(); });
    await flush();
    steps[id] = [...view.container.querySelectorAll(".tl-body")].flatMap((body) => [...body.children])
      .filter((element) => element.classList.contains("tl-step"))
      .map((element) => element.getAttribute("data-msg-id") || "function");
    await view.unmount();
  }
  return steps;
}

function normalizeMarkup(html) {
  return html
    .replace(/<time\b[^>]*>.*?<\/time>/g, "")
    .replace(/\s(?:title|datetime|data-tip|data-agent-id|stroke-dasharray|stroke-dashoffset|pathLength)="[^"]*"/g, "")
    .replace(/\saria-label="\d+\/\d+\/\d+,[^"]*"/g, "")
    .replace(/\b(?:\d{1,2}:\d{2}(?:\s?[AP]M)?)\b/g, "")
    .replace(/radix-[^"\s]*/g, "radix");
}

function isBlank(value) {
  if (value === undefined || value === null || value === "" || value === false) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.values(value).every(isBlank);
  return false;
}

function diff(live, cold, path, out) {
  if (isBlank(live) && isBlank(cold)) return;
  if (live && cold && typeof live === "object" && typeof cold === "object" && Array.isArray(live) === Array.isArray(cold)) {
    for (const key of new Set([...Object.keys(live), ...Object.keys(cold)])) {
      if (path.length === 0 && CLIENT_ONLY_FIELDS.has(key)) continue;
      if (READ_ONLY_FIELDS.has(key) || (path.at(-2) === "blocks" && READ_ONLY_BLOCK_FIELDS.has(key))) continue;
      diff(live[key], cold[key], [...path, key], out);
    }
    return;
  }
  if (live !== cold) out.push({ path: path.join("."), live, cold });
}

/** The stretch of the cold transcript a window of ``live`` must hold in full. */
function heldStretch(live, cold) {
  const held = cold.ids.flatMap((id, index) => (live.rows[id] ? [index] : []));
  return held.length ? cold.ids.slice(held[0], held.at(-1) + 1) : [];
}

/** Everywhere the live view and the cold view disagree, row by row. A partial
 *  view may stop short of either end but has no gaps inside what it holds. */
export function compareViews(live, cold, { partial }) {
  const mismatches = [];
  const coldIds = partial ? heldStretch(live, cold) : cold.ids;
  const liveIds = partial ? live.ids.filter((id) => cold.rows[id]) : live.ids;
  for (const id of liveIds) {
    if (!cold.rows[id]) mismatches.push({ row: id, kind: "only-live", path: "", live: live.rows[id] });
  }
  for (const id of coldIds) {
    if (!live.rows[id]) mismatches.push({ row: id, kind: "only-cold", path: "", cold: cold.rows[id] });
  }
  if (partial) {
    for (const id of live.ids) {
      if (!cold.rows[id]) mismatches.push({ row: id, kind: "only-live", path: "", live: live.rows[id] });
    }
  }
  // An edge row's actions depend on a neighbour the window does not hold.
  const edges = partial ? new Set([live.ids[0], ...live.ids.slice(-2)]) : new Set();
  const shared = liveIds.filter((id) => cold.rows[id]);
  const coldOrder = coldIds.filter((id) => live.rows[id]);
  if (shared.join("\n") !== coldOrder.join("\n")) {
    mismatches.push({ row: "*", kind: "order", path: "", live: shared, cold: coldOrder });
  }
  for (const id of shared) {
    const found = [];
    diff(live.rows[id], cold.rows[id], [], found);
    for (const item of found) mismatches.push({ row: id, kind: "field", ...item });
    if (live.dom[id] !== cold.dom[id] && !edges.has(id)) {
      mismatches.push({ row: id, kind: "markup", path: "", live: live.dom[id], cold: cold.dom[id] });
    }
  }
  return mismatches;
}

function assignPath(target, path, value) {
  const keys = path.split(".");
  const leaf = keys.pop();
  let at = target;
  for (const [index, key] of keys.entries()) {
    at[key] ??= /^\d+$/.test(keys[index + 1] ?? leaf) ? [] : {};
    at = at[key];
  }
  if (value !== undefined) at[leaf] = value;
  else if (Array.isArray(at)) at.splice(Number(leaf), 1);
  else delete at[leaf];
}

/** Does the cold row, given only the live row's differing fields, draw the live
 *  markup? If not, the drawn difference has a cause no field comparison saw. */
async function fieldsExplainMarkup(app, id, live, cold, fields) {
  const row = structuredClone(cold.rows[id]);
  for (const key of CLIENT_ONLY_FIELDS) row[key] = live.rows[id][key];
  // Last first, so removing an array item leaves earlier indexes valid.
  for (const field of [...fields].reverse()) assignPath(row, field.path, field.live);
  const { rowsByView } = app.useSessionStore.getState();
  app.useSessionStore.setState({
    rowsByView: { ...rowsByView, [SESSION_ID]: { ...rowsByView[SESSION_ID], [id]: row } },
  });
  try {
    const view = await render(app.createElement(app.MessageRow, { id, sessionIdOverride: SESSION_ID }));
    const markup = normalizeMarkup(view.container.innerHTML);
    await view.unmount();
    return markup === live.dom[id];
  } finally {
    app.useSessionStore.setState({ rowsByView });
  }
}

async function withColdView(app, frames, use) {
  resetClient(app);
  const { host, socket } = await mount(app);
  await answerRead(app, socket, frames);
  const view = await captureView(app);
  const result = await use(view);
  socket.onclose = null;
  await host.unmount();
  return result;
}

/** Read one stored transcript cold and return the client's rows and markup,
 *  and what each row's opened strips draw when ``steps`` is set. */
export async function openStored(frames, { steps = false } = {}) {
  const app = await boot();
  return withColdView(app, frames, async (view) => (steps ? { ...view, steps: await captureSteps(app, view.ids) } : view));
}

/** Open a stored transcript on a live client, then hand ``use`` the socket:
 *  ``deliver`` a frame, ``answer`` the read the client asked for, read what it
 *  ``sent``, and read its current ``rows`` and ``ids``. */
export async function withOpenChat(frames, use) {
  const app = await boot();
  resetClient(app);
  const { host, socket } = await mount(app);
  await answerRead(app, socket, frames);
  const sent = () => app.client.sent.filter((entry) => entry.socket === socket).map((entry) => entry.message);
  const state = () => app.useSessionStore.getState();
  try {
    return await use({
      deliver: async (frame) => { deliver(socket, frame); await settle(app); },
      answer: (next) => answerRead(app, socket, next),
      deliverTogether: async (group) => { for (const frame of group) deliver(socket, frame); await settle(app); },
      answerNextPage: (next) => { app.client.answer = next; },
      sent,
      ids: () => [...(state().messageOrder[SESSION_ID] ?? [])],
      row: (id) => app.selectRow(SESSION_ID, id)(state()),
    });
  } finally {
    socket.onclose = null;
    await host.unmount();
  }
}

/** Compare a live view with a cold read of ``frames``. A markup mismatch says
 *  whether the row's own field differences account for it (``explained``). */
export async function compareWithCold(live, frames, { partial }) {
  const app = await boot();
  return withColdView(app, frames, async (cold) => {
    const mismatches = compareViews(live, cold, { partial });
    for (const mismatch of mismatches.filter((m) => m.kind === "markup")) {
      const fields = mismatches.filter((m) => m.kind === "field" && m.row === mismatch.row);
      mismatch.explained = await fieldsExplainMarkup(app, mismatch.row, live, cold, fields);
    }
    return mismatches;
  });
}

/** Replay one recording; returns each checkpoint with its mismatches. */
export async function replayRecording(recording) {
  const app = await boot();
  resetClient(app);
  let { host, socket } = await mount(app);
  const checkpoints = [];
  const compose = (step) => {
    const at = Date.now();
    const id = `pending_${step.client_request_id}`;
    app.appendLocalUserTurn(SESSION_ID, id, step.text, undefined, at, "pending");
    app.setPendingUserText(SESSION_ID, step.text, at, {
      messageId: id, requestId: step.client_request_id, hasAttachments: false,
      onAck: () => {}, onReject: () => {},
    });
  };
  for (const step of recording.timeline) {
    if (step.reopen) {
      socket.onclose = null;
      await host.unmount();
      resetClient(app);
      ({ host, socket } = await mount(app));
    } else if (step.compose) compose(step.compose);
    else if (step.frame) deliver(socket, step.frame);
    else if (step.read) await answerRead(app, socket, step.read.frames);
    else if (step.page) {
      app.client.answer = step.page.frames;
      await app.loadSessionHistoryWindow(SESSION_ID, step.page.direction, step.page.around ?? undefined);
      await settle(app);
    } else if (step.disconnect) {
      const down = socket;
      down.close();
      socket = await waitFor(() => app.client.sockets.find((s) => s !== down && s.readyState === 1 && !s.seen && (s.seen = true)), "the reconnect");
      socket.onopen?.();
      await settle(app);
    } else if (step.checkpoint) {
      checkpoints.push({ ...step.checkpoint, live: await captureView(app) });
    }
    if (step.frame) await flush();
  }
  const stranded = app.client.sent.filter((entry) => entry.socket === socket && !entry.answered
    && entry.message.action === "load_session" && !READ_FIELDS.some((field) => field in entry.message)).length;
  const reads = app.client.sent.filter((entry) => entry.socket === socket && entry.message.action === "load_session").length;
  checkpoints.push({ name: "end", partial: checkpoints.at(-1)?.partial ?? false, cold: recording.cold, stranded, reads, live: await captureView(app) });
  socket.onclose = null;
  await host.unmount();
  for (const checkpoint of checkpoints) {
    checkpoint.mismatches = await compareWithCold(checkpoint.live, checkpoint.cold, { partial: checkpoint.partial });
    delete checkpoint.cold;
  }
  return checkpoints;
}
