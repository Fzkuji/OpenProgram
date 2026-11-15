import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { parseHTML } = require("linkedom");
const find = await import("../../../lib/chat/transcript/conversation-find.ts");

const matches = find.normalizeMatches([
  { id: "u1", message_id: "u1", role: "user", count: 1, snippet: "a" },
  { id: "a1", message_id: "a1", role: "assistant", count: 3, snippet: "b" },
  { id: 7, message_id: "bad" },
  null,
]);

test("matches number every occurrence across messages", () => {
  assert.deepEqual(matches.map((m) => m.message_id), ["u1", "a1"]);
  assert.equal(find.totalOccurrences(matches), 4);
  assert.deepEqual(find.locate(matches, 0), { match: matches[0], occurrence: 0 });
  assert.deepEqual(find.locate(matches, 3), { match: matches[1], occurrence: 2 });
  assert.equal(find.locate(matches, 4), null);
  assert.equal(find.locate(matches, -1), null);
});

test("stepping wraps at both ends", () => {
  assert.equal(find.step(-1, 4, true), 0);
  assert.equal(find.step(-1, 4, false), 3);
  assert.equal(find.step(3, 4, true), 0);
  assert.equal(find.step(0, 4, false), 3);
  assert.equal(find.step(0, 0, true), -1);
});

test("text ranges are case-insensitive and skip hidden text", () => {
  const { document } = parseHTML(`<div id="root"><p>Alpha <b>alpha</b> ALPHAalpha</p><pre hidden>alpha</pre></div>`);
  // linkedom's Range has no setStart/setEnd.
  document.createRange = () => ({
    setStart(node, offset) { this.startContainer = node; this.startOffset = offset; },
    setEnd(_node, offset) { this.endOffset = offset; },
  });
  const ranges = find.textRanges(document.getElementById("root"), "alpha");
  assert.deepEqual(
    ranges.map((r) => [r.startContainer.nodeValue, r.startOffset, r.endOffset]),
    [["Alpha ", 0, 5], ["alpha", 0, 5], [" ALPHAalpha", 1, 6], [" ALPHAalpha", 6, 11]],
  );
  assert.deepEqual(find.textRanges(document.getElementById("root"), ""), []);
});

test("the chat owns Cmd+F except inside terminals, editors and split panes", () => {
  const { document } = parseHTML(`<div id="chatView"><div id="chatArea"></div><div class="cm-editor"><i id="composer"></i></div></div>
    <div class="xterm"><i id="term"></i></div><div class="cm-editor"><i id="file"></i></div>
    <div class="peer-session-pane"><i id="peer"></i></div>`);
  const area = document.getElementById("chatArea");
  assert.equal(find.chatOwnsFind(null, area), true);
  assert.equal(find.chatOwnsFind(document.getElementById("composer"), area), true);
  assert.equal(find.chatOwnsFind(document.getElementById("term"), area), false);
  assert.equal(find.chatOwnsFind(document.getElementById("file"), area), false);
  assert.equal(find.chatOwnsFind(document.getElementById("peer"), area), false);
  assert.equal(find.chatOwnsFind(null, null), false);
});

test("highlights register under the find names and clear together", () => {
  const set = new Map();
  globalThis.CSS = { highlights: { set: (k, v) => set.set(k, v), delete: (k) => set.delete(k) }, escape: (s) => s };
  globalThis.Highlight = class { constructor(...ranges) { this.ranges = ranges; } };
  try {
    find.paintHighlights(["r1", "r2"], "r2");
    assert.deepEqual(set.get(find.FIND_ALL).ranges, ["r1", "r2"]);
    assert.deepEqual(set.get(find.FIND_CURRENT).ranges, ["r2"]);
    find.paintHighlights(["r1"], null);
    assert.equal(set.has(find.FIND_CURRENT), false);
    find.clearHighlights();
    assert.equal(set.size, 0);
  } finally {
    delete globalThis.CSS;
    delete globalThis.Highlight;
  }
});

test("a folded row opens for a match and stops answering once it is gone", () => {
  let opened = 0;
  assert.equal(find.revealForFind("m1"), false);
  const forget = find.onFindReveal("m1", () => { opened += 1; });
  const forgetOther = find.onFindReveal("m1", () => { opened += 10; });
  assert.equal(find.revealForFind("m1"), true);
  assert.equal(opened, 11);
  assert.equal(find.revealForFind("m2"), false);
  forget();
  forgetOther();
  assert.equal(find.revealForFind("m1"), false);
  assert.equal(opened, 11);
});
