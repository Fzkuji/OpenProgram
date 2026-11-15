import assert from "node:assert/strict";
import test from "node:test";

import { commentChatKey, pageCommentText, pickedElementName } from "../../lib/browser/capture/page-comment.ts";

const pick = (fields) => ({
  id: "w:1", url: "https://example.com/a", title: "Example", tag: "button",
  selector: "main > button:nth-of-type(2)", text: "", label: "", href: "", ...fields,
});

test("the comment names the page, the element and its text", () => {
  assert.equal(
    pageCommentText(pick({ text: "  Save\n changes " }), " Make this blue \n"),
    'Comment on [Example](https://example.com/a), element `main > button:nth-of-type(2)` ("Save changes"):\nMake this blue',
  );
});

test("a link's target is kept, and long text is clipped", () => {
  const out = pageCommentText(pick({ tag: "a", text: "x".repeat(200), href: "https://example.com/b" }), "broken");
  assert.match(out, /\("x{119}…"\) → https:\/\/example\.com\/b:\nbroken$/);
});

test("an untitled page falls back to its URL and an unnamed element to its tag", () => {
  assert.equal(
    pageCommentText(pick({ title: "", selector: "" }), "hi"),
    "Comment on https://example.com/a, element <button>:\nhi",
  );
  assert.equal(pickedElementName(pick({})), "<button>");
  assert.equal(pickedElementName(pick({ label: "Close dialog" })), "Close dialog");
});

test("the comment goes to the first conversation that is still open", () => {
  const open = new Set(["chat-b", "chat-c"]);
  const isOpen = (key) => open.has(key);
  assert.equal(commentChatKey([null, "chat-b", "chat-c"], isOpen), "chat-b");
  assert.equal(commentChatKey(["chat-gone", undefined, "chat-c"], isOpen), "chat-c");
  assert.equal(commentChatKey([null, undefined, "chat-gone"], isOpen), null);
});
