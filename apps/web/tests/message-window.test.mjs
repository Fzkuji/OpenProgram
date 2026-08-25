import assert from "node:assert/strict";
import test from "node:test";

import { readFileSync } from "node:fs";

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
} from "../lib/state/message-window.ts";

const listSrc = readFileSync(
  new URL("../components/chat/messages/message-list.tsx", import.meta.url),
  "utf8",
);
const peerSrc = readFileSync(
  new URL("../components/chat/peer-session-pane.tsx", import.meta.url),
  "utf8",
);
const shellSrc = readFileSync(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);

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

test("hidden chat host does not map MessageRow", () => {
  assert.match(shellSrc, /hostPaintsRows\(showChat, sessionPaneIndex, activeTabDagView\)/);
  assert.match(shellSrc, /<MessageList paintRows=\{paintRows\}/);
  assert.match(listSrc, /paintRows = true/);
  const mapAt = listSrc.indexOf("liveSet && chatKey");
  const gateAt = listSrc.lastIndexOf("paintRows ?", mapAt);
  assert.ok(mapAt > 0 && gateAt >= 0 && gateAt < mapAt);
  assert.match(listSrc, /\{paintRows \? \(\(\) => \{/);
});

test("peer pane recycles with its own height bucket", () => {
  assert.match(peerSrc, /RecyclableRow/);
  assert.match(peerSrc, /peer:\$\{sessionId\}/);
  assert.match(peerSrc, /heightsFor\(chatKey\)/);
  assert.match(peerSrc, /chatKey=\{chatKey\}/);
  assert.match(peerSrc, /noteChatWidth\(chatKey/);
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

test("known container width change drops that chatKey only", () => {
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
  assert.equal(getRowHeight("wide", "m1"), undefined);
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

test("list and peer note container width before recycle", () => {
  assert.match(listSrc, /noteChatWidth\(chatKey, areaW\)/);
  assert.match(listSrc, /noteChatWidth\(chatKey, area\.clientWidth\)/);
});
