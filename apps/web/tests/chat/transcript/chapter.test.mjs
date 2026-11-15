// A chapter pin rides on the turn's user message (the server projects it as
// chapterPinnedAt, old_shapes chapter_pin); a reply finds the user message
// that opened its turn to pin it.
import assert from "node:assert/strict";
import test from "node:test";
import { installLoader } from "../../_support/load.mjs";

installLoader();
const { turnUserMessageId } = await import("../../../lib/chat/transcript/chapter.ts");

test("a reply's turn is the nearest user message above it, past runtime rows", () => {
  const byId = {
    u1: { role: "user" }, a1: { role: "assistant" },
    u2: { role: "user" }, rt: { role: "user", rowKind: "silent" }, a2: { role: "assistant" },
  };
  const order = ["u1", "a1", "u2", "rt", "a2"];
  assert.equal(turnUserMessageId(order, byId, "a1"), "u1");
  assert.equal(turnUserMessageId(order, byId, "a2"), "u2");
  assert.equal(turnUserMessageId(["a0", ...order], byId, "a0"), undefined);
  assert.equal(turnUserMessageId(undefined, byId, "a1"), undefined);
});
