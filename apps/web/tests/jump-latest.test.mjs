import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CHAT_AT_BOTTOM_EPSILON,
  chatAtBottomSlack,
  isChatAtBottom,
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

test("last message above the composer pad counts as at bottom", () => {
  const area = { scrollHeight: 2000, scrollTop: 820, clientHeight: 1000 };
  // remaining = 180, pad = 180 → still at latest (epsilon)
  assert.equal(remainingScroll(area), 180);
  assert.equal(isChatAtBottom(area, 180), true);
  assert.equal(isChatAtBottom(area, 80), false);
});

test("scrolled further up than the pad is detached", () => {
  const area = { scrollHeight: 2000, scrollTop: 600, clientHeight: 1000 };
  assert.equal(remainingScroll(area), 400);
  assert.equal(isChatAtBottom(area, 180), false);
});

test("true flush bottom is at bottom even with no pad", () => {
  const area = { scrollHeight: 1000, scrollTop: 0, clientHeight: 1000 };
  assert.equal(isChatAtBottom(area, 0), true);
  assert.equal(chatAtBottomSlack(0), CHAT_AT_BOTTOM_EPSILON);
});

test("jump button is portaled onto the scrollport, not sticky in the stream", () => {
  assert.match(messageList, /createPortal/);
  assert.match(messageList, /getElementById\("chatArea"\)/);
  assert.match(messageList, /isChatAtBottom/);
  assert.doesNotMatch(messageList, /clientHeight < 80/);
  assert.match(jumpCss, /position: absolute;/);
  assert.doesNotMatch(jumpCss, /position: sticky;/);
  assert.match(jumpCss, /--main-composer-height/);
  assert.match(jumpCss, /jump-latest-live/);
});

test("live turn shows the in-progress bars on the jump button", () => {
  assert.match(messageList, /jump-latest-live/);
  assert.match(messageList, /runningTask \? \(/);
});
