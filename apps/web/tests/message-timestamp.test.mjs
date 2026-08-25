import assert from "node:assert/strict";
import test from "node:test";

import {
  validMessageTimestamp,
  withMessageTimestamp,
} from "../lib/session-store/message-timestamp.ts";
import { messagePatchUnchanged } from "../lib/session-store/message-patch.ts";

test("already-stamped tree keeps identity", () => {
  const child = { id: "c", role: "assistant", content: "x", timestamp: 11 };
  const card = { id: "k", role: "assistant", content: "y", timestamp: 11 };
  const msg = {
    id: "p",
    role: "assistant",
    content: "hello",
    timestamp: 11,
    runtimeChildren: [child],
    attachCards: [card],
  };
  const out = withMessageTimestamp(msg, 99);
  assert.equal(out, msg);
  assert.equal(out.runtimeChildren, msg.runtimeChildren);
  assert.equal(out.runtimeChildren[0], child);
  assert.equal(out.attachCards[0], card);
});

test("missing child timestamp is filled; parent and lists are new", () => {
  const child = { id: "c", role: "assistant", content: "x" };
  const msg = {
    id: "p",
    role: "assistant",
    content: "hello",
    timestamp: 20,
    runtimeChildren: [child],
  };
  const out = withMessageTimestamp(msg);
  assert.notEqual(out, msg);
  assert.notEqual(out.runtimeChildren, msg.runtimeChildren);
  assert.notEqual(out.runtimeChildren[0], child);
  assert.equal(out.timestamp, 20);
  assert.equal(out.runtimeChildren[0].timestamp, 20);
  assert.equal(withMessageTimestamp(out), out);
});

test("updateMessage-style spread keeps stamped children", () => {
  const child = { id: "c", role: "assistant", content: "x", timestamp: 5 };
  const cur = {
    id: "p",
    role: "assistant",
    content: "hel",
    timestamp: 5,
    runtimeChildren: [child],
  };
  const patch = { content: "hello" };
  assert.equal(messagePatchUnchanged(cur, patch), false);
  const next = withMessageTimestamp({ ...cur, ...patch });
  assert.equal(next.content, "hello");
  assert.equal(next.timestamp, 5);
  assert.equal(next.runtimeChildren, cur.runtimeChildren);
  assert.equal(next.runtimeChildren[0], child);
});

test("identical stream patch still short-circuits before stamp", () => {
  const cur = { id: "m1", role: "assistant", content: "hello", timestamp: 1 };
  assert.equal(messagePatchUnchanged(cur, { content: "hello" }), true);
});

test("validMessageTimestamp rejects junk", () => {
  assert.equal(validMessageTimestamp(1), true);
  assert.equal(validMessageTimestamp(0), false);
  assert.equal(validMessageTimestamp(NaN), false);
  assert.equal(validMessageTimestamp(undefined), false);
});
