import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CHAT_AT_BOTTOM_EPSILON,
  chatAtBottomSlack,
  isChatAtBottom,
  JUMP_V_MAX,
  jumpMotionPlan,
  jumpScrollTopAt,
  jumpTraveled,
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

test("short hops never reach cruise speed", () => {
  const short = jumpMotionPlan(400);
  assert.equal(short.kind, "triangle");
  assert.ok(short.vPeak < JUMP_V_MAX);
  assert.equal(short.tCruise, 0);
  assert.ok(Math.abs(jumpTraveled(short, short.duration) - 400) < 0.5);
});

test("long hops cruise at the speed cap", () => {
  const long = jumpMotionPlan(12000);
  assert.equal(long.kind, "trapezoid");
  assert.equal(long.vPeak, JUMP_V_MAX);
  assert.ok(long.tCruise > 0);
  assert.ok(long.duration > jumpMotionPlan(400).duration);
  assert.equal(jumpScrollTopAt(0, 12000, 0), 0);
  assert.ok(Math.abs(jumpScrollTopAt(0, 12000, long.duration) - 12000) < 1);
});

test("jump keeps the button until the ride finishes", () => {
  const start = messageList.indexOf("const jumpToLatest");
  const jump = messageList.slice(start, messageList.indexOf("return { detached, jumpToLatest }"));
  assert.match(jump, /animateJumpToLatest/);
  assert.doesNotMatch(jump, /behavior: "smooth"/);
  assert.match(messageList, /Stay visible until the ease-in-out ride finishes/);
});

test("MessageList reads detached only after useChatAreaStick", () => {
  const list = messageList.slice(messageList.indexOf("export function MessageList"));
  const stick = list.indexOf("useChatAreaStick(");
  const fade = list.indexOf("const want = detached");
  assert.ok(stick >= 0 && fade > stick, "detached fade must follow useChatAreaStick");
});
