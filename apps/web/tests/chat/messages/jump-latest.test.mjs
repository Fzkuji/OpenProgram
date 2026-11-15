import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { readSplitSource } from "../../../scripts/testing/feature-source.mjs";

import {
  CHAT_AT_BOTTOM_EPSILON,
  chatAtBottomSlack,
  isChatAtBottom,
  remainingScroll,
} from "../../../lib/chat/history/chat-scroll.ts";

const messageList = readFileSync(new URL("../../../components/chat/messages/list/use-chat-area-stick.ts", import.meta.url), "utf8") + readSplitSource("components/chat/messages/list/message-list.tsx");

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

test("MessageList reads detached only after useChatAreaStick", () => {
  const list = messageList.slice(messageList.indexOf("function MessageList"));
  const stick = list.indexOf("useChatAreaStick(");
  const fade = list.indexOf("const want = paintRows && detached");
  assert.ok(stick >= 0 && fade > stick, "detached fade must follow useChatAreaStick");
});
