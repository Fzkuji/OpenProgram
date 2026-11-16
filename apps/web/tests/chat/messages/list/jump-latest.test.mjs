import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { readSplitSource } from "../../../../scripts/testing/feature-source.mjs";

import {
  AT_LATEST_PX,
  JUMP_HIDE_DISTANCE_PX,
  JUMP_SHOW_DISTANCE_PX,
  followAfterGrowth,
  followAfterScroll,
  jumpVisible,
  remainingScroll,
} from "../../../../lib/chat/history/chat-scroll.ts";

const messageList = readFileSync(new URL("../../../../components/chat/messages/list/use-chat-area-stick.ts", import.meta.url), "utf8") + readSplitSource("components/chat/messages/list/message-list.tsx");

test("following starts only at the bottom itself; the Jump distances are fixed", () => {
  assert.equal(AT_LATEST_PX, 2);
  assert.equal(JUMP_HIDE_DISTANCE_PX, 70);
  assert.equal(JUMP_SHOW_DISTANCE_PX, 240);
  const area = { scrollHeight: 2000, scrollTop: 998, clientHeight: 1000 };
  assert.equal(remainingScroll(area), 2);
  const follows = (remaining) => followAfterScroll({ following: false, latestWindow: true, remaining, movedUp: false });
  assert.equal(follows(remainingScroll(area)), true);
  assert.equal(follows(remainingScroll({ ...area, scrollTop: 997 })), false);
  assert.equal(follows(remainingScroll({ scrollHeight: 1000, scrollTop: 0, clientHeight: 1000 })), true);
});

test("a reader who stops short of the bottom is not pulled to it", () => {
  const at = (remaining, movedUp, following = true) =>
    followAfterScroll({ following, latestWindow: true, remaining, movedUp });
  assert.equal(at(300, false), true, "a scroll event from growth keeps following");
  assert.equal(at(40, true), false, "any move up stops following");
  assert.equal(at(300, false, false), false, "a reader in history stays there");
  assert.equal(at(70, false, false), false, "near the bottom is not the bottom");
  assert.equal(at(30, false, false), false);
  assert.equal(at(2, false, false), true, "reaching the bottom follows again");
  assert.equal(at(0, true), true, "at the bottom the view follows");
  assert.equal(followAfterScroll({ following: true, latestWindow: false, remaining: 0, movedUp: false }), false,
    "a window without the newest page is never the latest");
});

test("growth alone never stops following", () => {
  const grow = (remaining, readerActive, following = true) =>
    followAfterGrowth({ following, latestWindow: true, remaining, readerActive });
  assert.equal(grow(5000, false), true, "a long stream, a page of older rows, measured rows");
  assert.equal(grow(200, true), true, "a stream under a held pointer");
  assert.equal(grow(241, true), false, "a block the reader opened that put the latest far below");
  assert.equal(grow(500, false, false), false, "growth does not pull a reader out of history");
  assert.equal(grow(30, false, false), false, "near the bottom is not the bottom");
  assert.equal(grow(0, false, false), true, "content that shrank to the bottom follows again");
});

test("Jump shows only a clear distance above the latest and hides on reaching it", () => {
  const show = (was, remaining, following = false, hasNewer = false) =>
    jumpVisible(was, { following, hasNewer, remaining });
  assert.equal(show(false, 100), false, "a small move up shows nothing");
  assert.equal(show(false, 240), false);
  assert.equal(show(false, 241), true);
  assert.equal(show(true, 100), true, "between the two distances it keeps what it had");
  assert.equal(show(true, 70), false, "reaching the latest hides it");
  assert.equal(show(true, 0), false);
  assert.equal(show(true, 5000, true), false, "never while the view follows the latest");
  assert.equal(show(false, 0, false, true), true, "a window without the newest page always offers it");
});

test("MessageList reads showJump only after useChatAreaStick", () => {
  const list = messageList.slice(messageList.indexOf("function MessageList"));
  const stick = list.indexOf("useChatAreaStick(");
  const fade = list.indexOf("const want = paintRows && showJump");
  assert.ok(stick >= 0 && fade > stick, "the button fade must follow useChatAreaStick");
});

test("the room under the last row is the composer plus a fixed gap, in main and split panes", () => {
  const css = readFileSync(new URL("../../../../app/styles/chat/pane/transcript.css", import.meta.url), "utf8");
  const pad = css.slice(css.indexOf(".chat-messages {")).match(/padding:\s*([^;]+);/)[1];
  assert.equal(pad, "24px 0 calc(var(--main-composer-height, 0px) + 24px) 0");
  assert.doesNotMatch(pad, /vh/, "a share of the window height made the gap differ per window and composer");
  const pane = readFileSync(new URL("../../../../components/chat/pane/peer-session-pane.tsx", import.meta.url), "utf8");
  assert.match(pane, /--peer-composer-h", `\$\{Math\.round\(h\) \+ 24\}px`/);
});

test("a conversation's first render mounts its last screen of rows", async () => {
  const { decideLiveRows } = await import("../../../../lib/chat/history/message-window.ts");
  const nodes = Array.from({ length: 60 }, (_, i) => ({ kind: "row", id: `r${i}` }));
  const heights = new Map(nodes.map((n) => [n.id, 400]));
  const live = decideLiveRows({ nodes, heights, scrollTop: Infinity, viewH: 800, overscan: 1600, always: new Set(), listLen: 60, recycleMin: 40 });
  assert.equal(live.has("r59"), true);
  assert.equal(live.has("r54"), true);
  assert.equal(live.has("r0"), false, "rows far above the end stay recycled");
});
