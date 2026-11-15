import assert from "node:assert/strict";
import test from "node:test";


import {
  CHAT_HEIGHTS_STORAGE_KEY,
  RECYCLE_MIN_ROWS,
  bindHeightStorage,
  clearHeights,
  collectAlwaysLive,
  decideLiveRows,
  flushHeightPersist,
  foldKey,
  forgetHeightMemory,
  getRowHeight,
  heightsFor,
  hostPaintsRows,
  noteChatWidth,
  offsetOf,
  setRowHeight,
} from "../../../lib/chat/history/message-window.ts";

function nodes(n) {
  return Array.from({ length: n }, (_, i) => ({ kind: "row", id: `m${i}` }));
}

function fill(chatKey, count, height) {
  for (let i = 0; i < count; i++) setRowHeight(chatKey, `m${i}`, height);
  return heightsFor(chatKey);
}

test("short lists stay fully mounted", () => {
  clearHeights();
  const heights = fill("short", 10, 80);
  const live = decideLiveRows({
    nodes: nodes(10),
    heights,
    scrollTop: 0,
    viewH: 200,
    always: new Set(),
    listLen: 10,
  });
  assert.equal(live, null);
  assert.ok(10 < RECYCLE_MIN_ROWS);
});

test("unmeasured rows stay mounted even when offscreen", () => {
  clearHeights();
  const n = RECYCLE_MIN_ROWS;
  const heights = fill("miss", 3, 100);
  const live = decideLiveRows({
    nodes: nodes(n),
    heights,
    scrollTop: 0,
    viewH: 200,
    always: new Set(),
    listLen: n,
    overscan: 50,
  });
  assert.ok(live);
  for (let i = 3; i < n; i++) assert.ok(live.has(`m${i}`), `m${i} unmeasured`);
});

test("measured offscreen rows recycle; last and rail target stay live", () => {
  clearHeights();
  const n = 50;
  const heights = fill("win", n, 100);
  const last = `m${n - 1}`;
  const live = decideLiveRows({
    nodes: nodes(n),
    heights,
    scrollTop: 0,
    viewH: 200,
    always: new Set([last, "m40"]),
    listLen: n,
    overscan: 100,
    lead: 0,
  });
  assert.ok(live);
  assert.ok(live.has("m0"));
  assert.ok(live.has("m1"));
  assert.ok(live.has(last));
  assert.ok(live.has("m40"));
  assert.equal(live.has("m20"), false);
});

test("compaction fold height sits in the offset chain", () => {
  clearHeights();
  const list = [
    { kind: "fold", id: "c0" },
    { kind: "row", id: "card" },
    { kind: "row", id: "tail" },
  ];
  const heights = new Map([
    [foldKey("c0"), 0],
    ["card", 80],
    ["tail", 120],
  ]);
  assert.equal(offsetOf(list, heights, "card", 0), 0);
  assert.equal(offsetOf(list, heights, "tail", 0), 80);
  assert.equal(offsetOf(list, heights, "missing", 0), null);
  const incomplete = new Map([["card", 80]]);
  assert.equal(offsetOf(list, incomplete, "tail", 0), null);
});

test("height cache does not leak across chats", () => {
  clearHeights();
  setRowHeight("a", "m1", 40);
  setRowHeight("b", "m1", 90);
  assert.equal(getRowHeight("a", "m1"), 40);
  assert.equal(getRowHeight("b", "m1"), 90);
  assert.equal(heightsFor("c").get("m1"), undefined);
  clearHeights("a");
  assert.equal(getRowHeight("a", "m1"), undefined);
  assert.equal(getRowHeight("b", "m1"), 90);
});

test("first write is first; same height is same", () => {
  clearHeights();
  assert.equal(setRowHeight("t", "x", 12), "first");
  assert.equal(setRowHeight("t", "x", 12), "same");
  assert.equal(setRowHeight("t", "x", 18), "update");
});

test("hostPaintsRows matches on-screen singleton transcript", () => {
  assert.equal(hostPaintsRows(true, 0, false), true);
  assert.equal(hostPaintsRows(false, 0, false), false);
  assert.equal(hostPaintsRows(true, -1, false), false);
  assert.equal(hostPaintsRows(true, 0, true), false);
});

test("collectAlwaysLive pins last, streaming, compaction card, extras", () => {
  const byId = {
    m0: { status: "done" },
    m1: { status: "streaming" },
    card: { kind: "compaction", slot: "card" },
    tail: { status: "done" },
  };
  const live = collectAlwaysLive(
    ["m0", "m1", "card", "tail"],
    (id) => byId[id],
    ["rail"],
  );
  assert.ok(live.has("m1"));
  assert.ok(live.has("card"));
  assert.ok(live.has("tail"));
  assert.ok(live.has("rail"));
  assert.equal(live.has("m0"), false);
});

test("peer pane recycles with its own height bucket", () => {
  clearHeights();
  setRowHeight("sess-1", "m1", 40);
  setRowHeight("peer:s1", "m1", 90);
  assert.equal(getRowHeight("sess-1", "m1"), 40);
  assert.equal(getRowHeight("peer:s1", "m1"), 90);
  assert.equal(heightsFor("peer:s1").get("m1"), 90);
});

function memStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => {
      m.set(k, String(v));
    },
  };
}

test("height cache persists per chatKey and reloads after remount", () => {
  const store = memStorage();
  bindHeightStorage(store);
  clearHeights();
  setRowHeight("a", "m1", 40);
  setRowHeight("b", "m1", 90);
  noteChatWidth("a", 800);
  assert.deepEqual(JSON.parse(store.getItem(CHAT_HEIGHTS_STORAGE_KEY) || "{}"), {});
  flushHeightPersist();
  const blob = JSON.parse(store.getItem(CHAT_HEIGHTS_STORAGE_KEY));
  assert.equal(blob.a.h.m1, 40);
  assert.equal(blob.a.w, 800);
  assert.equal(blob.b.h.m1, 90);
  forgetHeightMemory();
  assert.equal(getRowHeight("a", "m1"), 40);
  assert.equal(getRowHeight("b", "m1"), 90);
  bindHeightStorage(null);
  clearHeights();
});

test("corrupt and non-numeric height cache is dropped", () => {
  const store = memStorage();
  bindHeightStorage(store);
  clearHeights();
  store.setItem(CHAT_HEIGHTS_STORAGE_KEY, "nope");
  forgetHeightMemory();
  assert.equal(heightsFor("a").size, 0);
  store.setItem(
    CHAT_HEIGHTS_STORAGE_KEY,
    JSON.stringify({ a: { h: { m1: "tall", m2: 20, m3: NaN, m4: -1 } } }),
  );
  forgetHeightMemory();
  assert.equal(getRowHeight("a", "m1"), undefined);
  assert.equal(getRowHeight("a", "m2"), 20);
  assert.equal(getRowHeight("a", "m3"), undefined);
  assert.equal(getRowHeight("a", "m4"), undefined);
  bindHeightStorage(null);
  clearHeights();
});

test("known container width change keeps heights as estimates", () => {
  const store = memStorage();
  bindHeightStorage(store);
  clearHeights();
  setRowHeight("keep", "m1", 40);
  setRowHeight("wide", "m1", 90);
  noteChatWidth("keep", 800);
  noteChatWidth("wide", 800);
  flushHeightPersist();
  forgetHeightMemory();
  assert.equal(noteChatWidth("wide", 1000), true);
  assert.equal(getRowHeight("wide", "m1"), 90);
  assert.equal(noteChatWidth("wide", 1000), false);
  assert.equal(getRowHeight("keep", "m1"), 40);
  bindHeightStorage(null);
  clearHeights();
});

test("unknown width does not drop persisted heights", () => {
  const store = memStorage();
  bindHeightStorage(store);
  clearHeights();
  setRowHeight("a", "m1", 40);
  flushHeightPersist();
  forgetHeightMemory();
  assert.equal(noteChatWidth("a", 0), false);
  assert.equal(getRowHeight("a", "m1"), 40);
  assert.equal(noteChatWidth("a", 800), false);
  assert.equal(getRowHeight("a", "m1"), 40);
  bindHeightStorage(null);
  clearHeights();
});

// ── The transcript around a compaction: the summary card, the folded
// originals it covers, event dividers and pending rows, rendered through
// the real MessageList. Loaded lazily so the pure tests above stay DOM-free.
async function loadTranscript() {
  const { installLoader } = await import("../../_support/load.mjs");
  const { installDom, render } = await import("../../_support/dom.mjs");
  installLoader();
  // Reduced motion: folds and the summary expander switch without animating.
  installDom({ pathname: "/chat", media: (query) => query.includes("prefers-reduced-motion: reduce") });
  const { createElement } = await import("react");
  const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
  const { MessageList: List } = await import("../../../components/chat/messages/list/message-list.tsx");
  // The list asks useAgents whether this is an always-on conversation.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const MessageList = (props) => createElement(QueryClientProvider, { client }, createElement(List, props));
  const { useSessionStore } = await import("../../../lib/session-store/index.ts");
  const { loadSessionData } = await import("../../../lib/runtime-bridge/session/conversations.ts");
  const { runtimeState } = await import("../../../lib/runtime-bridge/state.ts");
  return { createElement, render, MessageList, useSessionStore, loadSessionData, runtimeState };
}
const transcript = loadTranscript();

/** The kind the server sends with each row (transcript/rows.py). */
const kindOf = (row) => row.rowKind ?? (row.role !== "system" ? row.role
  : row.slot === "card" ? "compaction_card" : row.kind ? "system_event" : "system_text");
function seedTranscript(useSessionStore, given, extra = {}) {
  const rows = given.map((row) => ({ ...row, rowKind: kindOf(row) }));
  useSessionStore.setState({
    currentSessionId: "mw", activeChatKey: "mw", runningTasks: {},
    rowsByView: { mw: Object.fromEntries(rows.map((row) => [row.id, row])) },
    messageOrder: { mw: rows.map((row) => row.id) },
    ...extra,
  });
}
const T = 1_700_000_000;
const compactedRows = () => [
  { id: "u1", role: "user", content: "first question", timestamp: T, coveredBy: "card" },
  { id: "a1", role: "assistant", content: "first answer", status: "done", timestamp: T, coveredBy: "card" },
  { id: "card", role: "system", kind: "compaction", slot: "card", summarisedCount: 2,
    content: "Summary of the **early** talk", timestamp: T },
  { id: "ev", role: "system", kind: "compaction", slot: "event", summarisedCount: 2, tokensBefore: 900, tokensAfter: 300,
    content: "", timestamp: T },
  { id: "u2", role: "user", content: "second question", timestamp: T },
  { id: "a2", role: "assistant", content: "second answer", status: "done", timestamp: T },
  { id: "u3", role: "user", content: "third question", timestamp: T },
  { id: "a3", role: "assistant", content: "third answer", status: "done", timestamp: T },
];

test("a compaction card folds its covered originals and keeps them off the rail until shown", async () => {
  const { createElement, render, MessageList, useSessionStore } = await transcript;
  seedTranscript(useSessionStore, compactedRows());
  const view = await render(createElement(MessageList));
  const $ = (selector) => view.container.querySelector(selector);
  try {
    const fold = $(".compaction-orig-fold");
    assert.equal(fold.getAttribute("data-open"), "0");
    assert.deepEqual([...fold.querySelectorAll("[data-msg-id]")].map((el) => el.getAttribute("data-msg-id")), ["u1", "a1"]);
    const ticks = () => [...view.container.querySelectorAll(".msg-rail-dash")].map((el) => el.getAttribute("aria-label"));
    assert.deepEqual(ticks(), ["second question", "third question"]);
    // The bar above the card: inert count text, then the originals toggle.
    const bar = $(".compaction-card-bar");
    assert.equal(bar.querySelector(".compaction-card-info").tagName, "SPAN");
    assert.match(bar.querySelector(".compaction-card-info").textContent, /^Compacted 2 earlier messages/);
    assert.equal($(".compaction-card .compaction-orig-toggle"), null);
    const toggle = bar.querySelector("button.compaction-orig-toggle");
    assert.equal(toggle.textContent, "Show original messages");
    await view.act(() => toggle.click());
    assert.equal($(".compaction-orig-fold").getAttribute("data-open"), "1");
    assert.equal(toggle.textContent, "Hide original messages");
    assert.deepEqual(ticks(), ["first question", "second question", "third question"]);
    await view.act(() => toggle.click());
    assert.equal($(".compaction-orig-fold").getAttribute("data-open"), "0");
  } finally { await view.unmount(); }
});

test("the summary card renders its markdown and expands with Show all", async () => {
  const { createElement, render, MessageList, useSessionStore } = await transcript;
  seedTranscript(useSessionStore, compactedRows());
  const view = await render(createElement(MessageList));
  try {
    const card = view.container.querySelector(".message.compaction-card");
    assert.ok(card.querySelector(".compaction-card-md strong"));
    const more = card.querySelector(".compaction-card-more");
    assert.equal(more.textContent, "… Show all");
    assert.equal(card.getAttribute("data-full"), "0");
    await view.act(() => more.click());
    assert.equal(card.getAttribute("data-full"), "1");
    assert.equal(more.textContent, "Collapse");
  } finally { await view.unmount(); }
});

test("a compaction event is a centred divider with no timestamp column", async () => {
  const { createElement, render, MessageList, useSessionStore } = await transcript;
  seedTranscript(useSessionStore, compactedRows());
  const view = await render(createElement(MessageList));
  try {
    const divider = view.container.querySelector('.message.system-event[data-msg-id="ev"]');
    assert.match(divider.querySelector(".system-event-text").textContent,
      /^Context compacted here: covered 2 messages, estimated 900 → 300 tokens/);
    assert.equal(divider.querySelectorAll(".system-event-rule").length, 2);
    assert.equal(divider.querySelector(".message-actions-footer"), null);
  } finally { await view.unmount(); }
});

test("transient system rows and the pending reply show a timestamp", async () => {
  const { createElement, render, MessageList, useSessionStore } = await transcript;
  seedTranscript(useSessionStore, [
    { id: "s1", role: "system", content: "/goal status: none", status: "done", timestamp: T },
    { id: "u9", role: "user", content: "go", timestamp: T },
  ], { runningTasks: { mw: { msg_id: "u9", started_at: T } } });
  const view = await render(createElement(MessageList));
  try {
    assert.ok(view.container.querySelector('.message.system[data-msg-id="s1"] .message-timestamp'));
    const pending = view.container.querySelector(".pending-standalone .pending-body");
    assert.ok(pending.querySelector(".message-timestamp"));
    assert.equal(view.container.querySelector(".pending-standalone .message-actions-footer"), null);
  } finally { await view.unmount(); }
});

test("no pending reply is shown once the last row is not the user's", async () => {
  const { createElement, render, MessageList, useSessionStore } = await transcript;
  seedTranscript(useSessionStore, [
    { id: "u9", role: "user", content: "go", timestamp: T },
    { id: "a9", role: "assistant", content: "", status: "streaming", timestamp: T },
  ], { runningTasks: { mw: { msg_id: "u9", started_at: T } } });
  const view = await render(createElement(MessageList));
  try {
    assert.equal(view.container.querySelector(".pending-standalone"), null);
  } finally { await view.unmount(); }
});


