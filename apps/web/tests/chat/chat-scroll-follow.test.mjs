import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  JUMP_PLACEMENT_TOLERANCE_PX,
  TAKE_LATEST_IDLE_LIMIT,
  chatAtBottomSlack,
  defaultScrollerKey,
  forgetTakeLatestSession,
  isChatAtBottom,
  isProvisionalSessionBind,
  lastSettledTakeLatest,
  latestScrollTop,
  noteTakeLatest,
  peekTakeLatest,
  readComposerOverlay,
  retainTakeLatestViewport,
  settleTakeLatest,
  snapToLatest,
  stopAreaScroll,
  subscribeTakeLatest,
  animateJumpToLatest,
} from "../../lib/chat/chat-scroll.ts";
import {
  historyAreaStillOwned,
  restoreAreaWindow,
  setFollowLock,
  isFollowLocked,
} from "../../lib/chat/history-viewport.ts";

test("draft-to-existing switch is not a provisional bind", () => {
  assert.equal(
    isProvisionalSessionBind("local_draft", "local_draft", "sess_real", "sess_real"),
    false,
  );
  assert.equal(
    isProvisionalSessionBind("local_draft", "local_draft", "local_draft", "local_draft"),
    true,
  );
  assert.equal(
    isProvisionalSessionBind("local_a", "local_a", "local_b", "local_b"),
    false,
  );
  assert.equal(isProvisionalSessionBind("local_draft", null, null, "sess_real"), false);
});

test("history restore requires session and chatKey ownership, not isConnected", () => {
  const area = { isConnected: true, id: "peer" };
  const cap = { area, chatKey: "s", fromRegistry: true };
  const registered = new Map([[area, "s"]]);
  assert.equal(
    historyAreaStillOwned(cap, registered, {
      sessionId: "s",
      focusedSessionId: "other",
      focusedChatKey: "other",
    }),
    true,
  );
  registered.set(area, "peer:s");
  assert.equal(
    historyAreaStillOwned(cap, registered, {
      sessionId: "s",
      focusedSessionId: "s",
      focusedChatKey: "s",
    }),
    false,
  );
  registered.delete(area);
  assert.equal(
    historyAreaStillOwned(cap, registered, {
      sessionId: "s",
      focusedSessionId: "s",
      focusedChatKey: "s",
    }),
    false,
    "a previously registered area that lost registration must not restore",
  );
  const main = { isConnected: true, id: "chatArea" };
  const previousDocument = globalThis.document;
  globalThis.document = {
    getElementById: (id) => (id === "chatArea" ? main : null),
  };
  try {
    assert.equal(
      historyAreaStillOwned(
        { area: main, chatKey: "s", fromRegistry: false },
        undefined,
        { sessionId: "s", focusedSessionId: "s", focusedChatKey: "s" },
      ),
      true,
    );
    assert.equal(
      historyAreaStillOwned(
        { area: { isConnected: true, id: "chatArea" }, chatKey: "s", fromRegistry: false },
        undefined,
        { sessionId: "s", focusedSessionId: "s", focusedChatKey: "s" },
      ),
      false,
      "fallback restore is only the live #chatArea node",
    );
  } finally {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});

test("deleting a session drops its take-latest records", () => {
  const sid = `del-${Date.now()}-${Math.random()}`;
  const note = noteTakeLatest({ sessionId: sid, scrollerKey: sid, turnSeed: "u" });
  settleTakeLatest(sid, sid, note.generation);
  assert.equal(peekTakeLatest(sid, sid)?.generation, note.generation);
  forgetTakeLatestSession(sid);
  assert.equal(peekTakeLatest(sid, sid), null);
  assert.equal(lastSettledTakeLatest(sid, sid), 0);
});

test("idle settled notes are capped; waiting notes and live viewports remain", () => {
  const waitingSid = `wait-${Date.now()}-${Math.random()}`;
  const waiting = noteTakeLatest({
    sessionId: waitingSid,
    scrollerKey: waitingSid,
    turnSeed: "w",
  });
  const liveSid = `live-${Date.now()}-${Math.random()}`;
  const live = noteTakeLatest({
    sessionId: liveSid,
    scrollerKey: liveSid,
    turnSeed: "l",
  });
  settleTakeLatest(liveSid, liveSid, live.generation);
  const release = retainTakeLatestViewport(liveSid, liveSid);
  const idlePrefix = `idle-${Date.now()}-${Math.random()}`;
  const firstIdle = `${idlePrefix}-0`;
  for (let i = 0; i < TAKE_LATEST_IDLE_LIMIT + 5; i++) {
    const sid = `${idlePrefix}-${i}`;
    const note = noteTakeLatest({ sessionId: sid, scrollerKey: sid, turnSeed: `t${i}` });
    settleTakeLatest(sid, sid, note.generation);
  }
  assert.equal(peekTakeLatest(waitingSid, waitingSid)?.generation, waiting.generation);
  assert.equal(lastSettledTakeLatest(liveSid, liveSid), live.generation);
  assert.equal(peekTakeLatest(firstIdle, firstIdle), null);
  assert.equal(lastSettledTakeLatest(firstIdle, firstIdle), 0);
  release();
});

test("releasing viewports prunes idle notes after this turn, not during remount", async () => {
  const prefix = `rel-${Date.now()}-${Math.random()}`;
  const ids = Array.from({ length: TAKE_LATEST_IDLE_LIMIT + 5 }, (_, i) => `${prefix}-${i}`);
  const present = () => ids.filter((id) => (
    peekTakeLatest(id, id) != null || lastSettledTakeLatest(id, id) > 0
  ));
  const releases = [];
  for (const sid of ids) {
    const note = noteTakeLatest({ sessionId: sid, scrollerKey: sid, turnSeed: sid });
    settleTakeLatest(sid, sid, note.generation);
    releases.push(retainTakeLatestViewport(sid, sid));
  }
  assert.equal(present().length, ids.length);
  for (const release of releases) release();
  assert.equal(present().length, ids.length, "prune waits so a remount can retain again");
  const keep = retainTakeLatestViewport(ids[0], ids[0]);
  await Promise.resolve();
  assert.ok(present().includes(ids[0]), "same-tick retain keeps the record");
  keep();
  await Promise.resolve();
  assert.ok(present().length <= TAKE_LATEST_IDLE_LIMIT);
});

test("F18 web production has no leftover follow writers", () => {
  const files = [
    "helpers.ts",
    "chat-handlers.ts",
    "conversations.ts",
    "state.ts",
    "session-history-loader.ts",
  ].map((name) => readFileSync(new URL(`../../lib/runtime-bridge/${name}`, import.meta.url), "utf8"));
  const joined = files.join("\n");
  assert.doesNotMatch(joined, /scrollToBottom/);
  assert.doesNotMatch(joined, /_skipScrollToBottom/);
  assert.doesNotMatch(joined, /stickToBottom/);
  assert.doesNotMatch(
    readFileSync(new URL("../../lib/runtime-bridge/session-history-loader.ts", import.meta.url), "utf8"),
    /direction===['"]latest['"]\) area\.scrollTop/,
  );
});

test("follow ownership contracts stay in the production files", () => {
  const stick = readFileSync(
    new URL("../../components/chat/messages/use-chat-area-stick.ts", import.meta.url),
    "utf8",
  );
  const loader = readFileSync(
    new URL("../../lib/runtime-bridge/session-history-loader.ts", import.meta.url),
    "utf8",
  );
  const store = readFileSync(
    new URL("../../lib/session-store/index.ts", import.meta.url),
    "utf8",
  );
  assert.match(stick, /isProvisionalSessionBind\(/);
  assert.doesNotMatch(
    stick,
    /outgoingKey\?\.startsWith\("local_"\)[\s\S]{0,120}!chatKey\.startsWith\("local_"\)/,
  );
  assert.match(stick, /typesetMath\(msgs\)/);
  assert.doesNotMatch(stick, /renderMathInChat\(\)/);
  assert.doesNotMatch(stick, /stopJump = \(correct/);
  assert.match(loader, /historyAreaStillOwned\(/);
  assert.match(loader, /captureAreaRestoreState\(area, chatKey, true\)/);
  assert.doesNotMatch(loader, /liveRegistered\?\.has\(cap\.area\) \|\| cap\.area\.isConnected/);
  assert.match(store, /forgetTakeLatestSession\(/);
});

test("F07 send during loading holds the lock then restore runs after unlock", () => {
  const area = {
    isConnected: true,
    scrollTop: 15,
    scrollHeight: 300,
    querySelectorAll: () => [],
    dispatchEvent() {},
  };
  setFollowLock("main", true);
  restoreAreaWindow(
    { area, chatKey: "main", anchor: null, oldTop: 15, oldHeight: 120, fromRegistry: true },
    "older",
  );
  assert.equal(area.scrollTop, 15);
  setFollowLock("main", false);
  restoreAreaWindow(
    { area, chatKey: "main", anchor: null, oldTop: 15, oldHeight: 120, fromRegistry: true },
    "older",
  );
  assert.equal(area.scrollTop, 15 + 300 - 120);
});

test("F07/F17 follow lock skips restore and each area keeps its own fallback", () => {
  const areaA = {
    isConnected: true,
    scrollTop: 20,
    scrollHeight: 200,
    querySelectorAll: () => [],
    dispatchEvent() {},
  };
  const areaB = {
    isConnected: true,
    scrollTop: 80,
    scrollHeight: 400,
    querySelectorAll: () => [],
    dispatchEvent() {},
  };
  setFollowLock("peer:a", true);
  assert.equal(isFollowLocked("peer:a"), true);
  restoreAreaWindow(
    { area: areaA, chatKey: "peer:a", anchor: null, oldTop: 20, oldHeight: 100 },
    "older",
  );
  assert.equal(areaA.scrollTop, 20);
  setFollowLock("peer:a", false);
  restoreAreaWindow(
    { area: areaA, chatKey: "peer:a", anchor: null, oldTop: 20, oldHeight: 100 },
    "older",
  );
  assert.equal(areaA.scrollTop, 20 + 200 - 100);
  restoreAreaWindow(
    { area: areaB, chatKey: "peer:b", anchor: null, oldTop: 80, oldHeight: 250 },
    "older",
  );
  assert.equal(areaB.scrollTop, 80 + 400 - 250);
});

test("F01 empty short overflow geometry and peer slack 24+8", () => {
  assert.equal(latestScrollTop({ scrollHeight: 0, scrollTop: 0, clientHeight: 0 }), 0);
  assert.equal(latestScrollTop({ scrollHeight: 400, scrollTop: 0, clientHeight: 800 }), 0);
  assert.equal(latestScrollTop({ scrollHeight: 2000, scrollTop: 0, clientHeight: 800 }), 1200);
  const area = { scrollTop: 0, scrollHeight: 2000, clientHeight: 800 };
  snapToLatest(area);
  assert.equal(area.scrollTop, 1200);
  const pad = 200;
  const overlay = 176;
  assert.equal(chatAtBottomSlack(pad, overlay), 24 + 8);
  assert.equal(isChatAtBottom({ scrollHeight: 2000, scrollTop: 1200, clientHeight: 800 }, pad, overlay), true);
  assert.equal(isChatAtBottom({ scrollHeight: 2000, scrollTop: 1160, clientHeight: 800 }, pad, overlay), false);
});

test("F02 independent main/peer notes, increasing generations, subscribe without a row", () => {
  const sessionId = `s-${Date.now()}-${Math.random()}`;
  const main = defaultScrollerKey(sessionId, false);
  const peer = defaultScrollerKey(sessionId, true);
  assert.equal(main, sessionId);
  assert.equal(peer, `peer:${sessionId}`);
  const seen = [];
  const stop = subscribeTakeLatest((note) => {
    if (note.sessionId === sessionId) seen.push(note);
  });
  const first = noteTakeLatest({ sessionId, scrollerKey: main, turnSeed: "u1" });
  const second = noteTakeLatest({ sessionId, scrollerKey: peer, turnSeed: "u2" });
  const third = noteTakeLatest({ sessionId, scrollerKey: main, turnSeed: "slash" });
  assert.equal(first.generation < second.generation, true);
  assert.equal(second.generation < third.generation, true);
  assert.equal(peekTakeLatest(sessionId, main)?.turnSeed, "slash");
  assert.equal(peekTakeLatest(sessionId, peer)?.turnSeed, "u2");
  assert.equal(peekTakeLatest(sessionId, main)?.generation, third.generation);
  assert.deepEqual(seen.map((n) => n.turnSeed), ["u1", "u2", "slash"]);
  const again = peekTakeLatest(sessionId, main);
  assert.equal(again?.generation, third.generation);
  stop();
});

test("readComposerOverlay uses composer root, not pad var", () => {
  const root = { offsetHeight: 176, isConnected: true };
  assert.equal(readComposerOverlay(null, root), 176);
  assert.equal(chatAtBottomSlack(176 + 24, readComposerOverlay(null, root)), 32);
});

test("cancel stops native motion and skips the settle correction", () => {
  const events = new Map();
  const area = {
    scrollTop: 10,
    scrollHeight: 2000,
    clientHeight: 800,
    scrollTo({ top, behavior }) {
      this.scrollTop = top;
      this.behavior = behavior;
    },
    addEventListener(type, fn) {
      events.set(type, fn);
    },
    removeEventListener(type) {
      events.delete(type);
    },
  };
  let done = 0;
  const cancel = animateJumpToLatest(area, () => { done += 1; }, {
    reducedMotion: false,
    getTarget: () => latestScrollTop(area),
  });
  assert.equal(area.behavior, "smooth");
  const started = area.scrollTop;
  area.scrollHeight = 3000;
  cancel();
  assert.equal(done, 0);
  assert.equal(area.scrollTop, started);
  events.get("scrollend")?.();
  assert.equal(done, 0);
});

test("watchdog corrects to the current target then completes", async () => {
  const events = new Map();
  const area = {
    scrollTop: 0,
    scrollHeight: 1000,
    clientHeight: 400,
    scrollTo({ top }) { this.scrollTop = top; },
    addEventListener(type, fn) { events.set(type, fn); },
    removeEventListener(type) { events.delete(type); },
  };
  let done = 0;
  animateJumpToLatest(area, () => { done += 1; }, {
    reducedMotion: false,
    getTarget: () => latestScrollTop(area),
  });
  area.scrollHeight = 1800;
  events.get("scrollend")?.();
  assert.equal(done, 1);
  assert.ok(Math.abs(area.scrollTop - latestScrollTop(area)) <= JUMP_PLACEMENT_TOLERANCE_PX);
});

test("reduced motion jump places instantly", () => {
  const area = {
    scrollTop: 10,
    scrollHeight: 2000,
    clientHeight: 800,
    scrollTo({ top, behavior }) {
      this.scrollTop = top;
      this.behavior = behavior;
    },
    addEventListener() {},
    removeEventListener() {},
  };
  let done = 0;
  animateJumpToLatest(area, () => { done += 1; }, { reducedMotion: true });
  assert.equal(done, 1);
  assert.equal(area.scrollTop, latestScrollTop(area));
  assert.notEqual(area.behavior, "smooth");
});

test("stopAreaScroll holds the current top", () => {
  const area = {
    scrollTop: 40,
    scrollTo({ top, behavior }) {
      this.scrollTop = top;
      this.behavior = behavior;
    },
  };
  stopAreaScroll(area);
  assert.equal(area.scrollTop, 40);
  assert.equal(area.behavior, "auto");
});
