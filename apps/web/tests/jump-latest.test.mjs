import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CHAT_AT_BOTTOM_EPSILON,
  chatAtBottomSlack,
  easeInOutCubic,
  isChatAtBottom,
  jumpScrollTopAt,
  remainingScroll,
} from "../lib/state/chat-scroll.ts";

const messageList = readFileSync(
  new URL("../components/chat/messages/message-list.tsx", import.meta.url),
  "utf8",
);
const jumpCss = readFileSync(
  new URL("../app/styles/chat/jump-latest.css", import.meta.url),
  "utf8",
);

test("last message just above the composer still counts as at bottom", () => {
  const area = { scrollHeight: 2000, scrollTop: 820, clientHeight: 1000 };
  // remaining = 180, pad = 300, composer = 120 → slack = 180 + 8
  assert.equal(remainingScroll(area), 180);
  assert.equal(isChatAtBottom(area, 300, 120), true);
  assert.equal(isChatAtBottom(area, 80), false);
});

test("last message tucked under the composer is detached", () => {
  const area = { scrollHeight: 2000, scrollTop: 600, clientHeight: 1000 };
  assert.equal(remainingScroll(area), 400);
  assert.equal(isChatAtBottom(area, 300, 120), false);
});

test("true flush bottom is at bottom even with no pad", () => {
  const area = { scrollHeight: 1000, scrollTop: 0, clientHeight: 1000 };
  assert.equal(isChatAtBottom(area, 0), true);
  assert.equal(chatAtBottomSlack(0), CHAT_AT_BOTTOM_EPSILON);
  assert.equal(chatAtBottomSlack(200, 120), 80 + CHAT_AT_BOTTOM_EPSILON);
});

test("jump button is portaled onto #chatView, not the scroller", () => {
  assert.match(messageList, /createPortal/);
  assert.match(messageList, /getElementById\("chatView"\)/);
  assert.doesNotMatch(messageList, /createPortal\([\s\S]*chatArea/);
  assert.match(messageList, /isChatAtBottom/);
  assert.match(messageList, /readComposerHeight/);
  assert.doesNotMatch(messageList, /clientHeight < 80/);
  assert.match(jumpCss, /position: absolute;/);
  assert.doesNotMatch(jumpCss, /position: sticky;/);
  assert.match(jumpCss, /#chatView/);
  assert.match(jumpCss, /--main-composer-height/);
  assert.match(jumpCss, /jump-latest-live/);
});

test("live turn shows the in-progress bars on the jump button", () => {
  assert.match(messageList, /jump-latest-live/);
  assert.match(messageList, /runningTask \? \(/);
});

test("jump button fades out instead of unmounting immediately", () => {
  assert.match(messageList, /JUMP_LATEST_FADE_MS = 280/);
  assert.match(messageList, /jumpingRef/);
  assert.match(messageList, /is-leaving/);
  assert.match(jumpCss, /is-leaving/);
  assert.match(jumpCss, /280ms/);
});

test("jump scroll eases in, then out", () => {
  assert.equal(easeInOutCubic(0), 0);
  assert.equal(easeInOutCubic(1), 1);
  assert.equal(easeInOutCubic(0.5), 0.5);
  assert.ok(easeInOutCubic(0.25) < 0.25);
  assert.ok(easeInOutCubic(0.75) > 0.75);
  assert.equal(jumpScrollTopAt(0, 100, 0), 0);
  assert.equal(jumpScrollTopAt(0, 100, 1), 100);
});

test("jump keeps the button until the ride finishes", () => {
  const start = messageList.indexOf("const jumpToLatest");
  const jump = messageList.slice(start, messageList.indexOf("return { detached, jumpToLatest }"));
  assert.match(jump, /animateJumpToLatest/);
  assert.doesNotMatch(jump, /behavior: "smooth"/);
  assert.match(messageList, /Stay visible until the ease-in-out ride finishes/);
});
