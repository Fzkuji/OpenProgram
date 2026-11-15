// The message rail subscribes through useShallow, which compares one level
// deep, so each row is packed into one string and unpacked on the way out.
// Message text and file paths may hold any character, so the row must
// round-trip exactly and pack deterministically.
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, setRect } from "../../_support/dom.mjs";

installLoader({
  replace: {
    "/components/chat/messages/turn-files/turn-files-chips.tsx": "export const TurnFilesChips = () => null;",
  },
});
installDom({ pathname: "/chat" });
const { createElement } = await import("react");
const { packRailRow, unpackRailRow } = await import("../../../components/chat/messages/navigation/message-rail-row.ts");
const { MessageRail } = await import("../../../components/chat/messages/navigation/message-rail.tsx");
const { selectRow, useSessionStore } = await import("../../../lib/session-store/index.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const transcript = transcriptRows(useSessionStore, selectRow);

const rows = [
  {
    id: "m1", content: "hello world  with   spaces", preview: "hello world with spaces",
    assistantId: "a1", assistantSummary: "sure thing", assistantTurnFiles: undefined,
    assistantFileWriteState: "attempted", assistantReverted: false, chapter: true,
  },
  {
    id: "m2", content: "line one\nline two\ttabbed\n\n```js\nconst a = \"b\";\n```", preview: "line one line two tabbed",
    assistantId: undefined, assistantSummary: undefined, assistantTurnFiles: undefined,
    assistantFileWriteState: "none", assistantReverted: false, chapter: false,
  },
  {
    id: "m3", content: "x", preview: "x", assistantId: undefined, assistantSummary: undefined,
    assistantTurnFiles: undefined, assistantFileWriteState: "failed", assistantReverted: false, chapter: false,
  },
  {
    id: "m4", content: "中文内容，带标点。🎯", preview: "中文内容，带标点。🎯", assistantId: "a4", assistantSummary: "回复摘要",
    assistantTurnFiles: {
      version: 2, files: [{ path: "src/a␟b\n\"文.ts", op: "modify", added: 1, removed: 0 }],
      file_count: 1, added: 1, removed: 0,
    },
    assistantFileWriteState: "none", assistantReverted: true, chapter: true,
  },
];

test("rows with spaces, newlines, quotes, fences, CJK and separators round-trip exactly", () => {
  for (const row of rows) assert.deepEqual(unpackRailRow(packRailRow(row)), row);
});

test("empty optional fields come back as undefined, not empty strings", () => {
  const got = unpackRailRow(packRailRow({ id: "m", content: "c", preview: "p" }));
  assert.equal(got.assistantId, undefined);
  assert.equal(got.assistantSummary, undefined);
  assert.equal(got.assistantTurnFiles, undefined);
  assert.equal(got.assistantFileWriteState, "none");
  assert.equal(got.assistantReverted, false);
  assert.equal(got.chapter, false);
});

test("legacy five-field separator rows stay readable", () => {
  const legacy = unpackRailRow(["old", "text", "preview", "assistant", "reply"].join("␟"));
  assert.equal(legacy.id, "old");
  assert.equal(legacy.assistantId, "assistant");
  assert.equal(legacy.assistantSummary, "reply");
  assert.equal(legacy.assistantFileWriteState, "none");
});

test("packing is deterministic, so an unchanged transcript compares equal", () => {
  assert.deepEqual(rows.map(packRailRow), rows.map(packRailRow));
  assert.notEqual(packRailRow(rows[0]), packRailRow({ ...rows[0], assistantReverted: true }));
  assert.notEqual(packRailRow(rows[1]), packRailRow({ ...rows[1], chapter: true }));
});

describe("message rail", () => {
  function seed(ids, pinned = []) {
    const seeded = {};
    const order = [];
    for (const id of ids) {
      seeded[id] = {
        id, role: "user", content: `question ${id}`,
        ...(pinned.includes(id) ? { chapterPinnedAt: 1_700_000_000 } : {}),
      };
      seeded[`${id}_reply`] = { id: `${id}_reply`, role: "assistant", content: `answer ${id}`, status: "done" };
      order.push(id, `${id}_reply`);
    }
    useSessionStore.setState({ currentSessionId: "s1", rowsByView: { s1: seeded }, messageOrder: { s1: order } });
  }

  test("draws one tick per user message and drops folded covered originals", async () => {
    seed(["u1", "u2", "u3"]);
    const view = await render(createElement(MessageRail, { hiddenKey: "u1" }));
    try {
      const ticks = [...view.container.querySelectorAll(".msg-rail-dash")];
      assert.deepEqual(ticks.map((tick) => tick.getAttribute("aria-label")), ["question u2", "question u3"]);
    } finally { await view.unmount(); }
  });

  test("a pinned turn's tick carries the chapter accent class; the others do not", async () => {
    seed(["u1", "u2", "u3"], ["u2"]);
    const view = await render(createElement(MessageRail, {}));
    try {
      const ticks = [...view.container.querySelectorAll(".msg-rail-dash")];
      assert.deepEqual(ticks.map((tick) => tick.classList.contains("chapter")), [false, true, false]);
      // At rest a chapter tick is longer than the others, which stay alike.
      assert.deepEqual(ticks.map((tick) => tick.style.width), ["9px", "18px", "9px"]);
    } finally { await view.unmount(); }
  });

  test("pinning a turn in the store retints its tick in place", async () => {
    seed(["u1", "u2"]);
    const view = await render(createElement(MessageRail, {}));
    try {
      await view.act(() => transcript.patchRow("s1", "u1", { chapterPinnedAt: 1_700_000_001 }));
      const [first, second] = view.container.querySelectorAll(".msg-rail-dash");
      assert.equal(first.classList.contains("chapter"), true);
      assert.equal(second.classList.contains("chapter"), false);
      await view.act(() => transcript.patchRow("s1", "u1", { chapterPinnedAt: undefined }));
      assert.equal(view.container.querySelector(".msg-rail-dash").classList.contains("chapter"), false);
    } finally { await view.unmount(); }
  });

  test("the rail sheet tints chapter ticks with the theme purple token only", async () => {
    const { readFile } = await import("node:fs/promises");
    const css = await readFile(new URL("../../../app/styles/chat/messages/message-rail.css", import.meta.url), "utf8");
    const rules = css.match(/\.msg-rail-dash\.chapter[^{]*\{[^}]*\}/g) ?? [];
    assert.ok(rules.length >= 2, "resting and hovered/current chapter rules");
    for (const rule of rules) {
      assert.match(rule, /var\(--accent-purple\)/);
      assert.doesNotMatch(rule, /#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i);
      // Width comes from the component (CHAPTER_W), never from the sheet.
      assert.doesNotMatch(rule, /width/);
    }
  });

  test("clicking a tick seeks to the real message node, but not into a folded original", async () => {
    seed(["u1", "u2"]);
    document.body.innerHTML = `<div id="chatArea"><div id="chatMessages">
      <div class="compaction-orig-fold" data-open="0"><div data-msg-id="u1"></div></div>
      <div data-msg-id="u2"></div></div></div>`;
    const scrolled = [];
    for (const node of document.querySelectorAll("[data-msg-id]")) {
      node.scrollIntoView = () => scrolled.push(node.getAttribute("data-msg-id"));
      setRect(node, { top: 0, height: 10 });
    }
    const seeks = [];
    const view = await render(createElement(MessageRail, { onSeek: (id) => seeks.push(id) }));
    try {
      const [first, second] = view.container.querySelectorAll(".msg-rail-dash");
      first.click();
      second.click();
      assert.deepEqual(seeks, ["u1", "u2"]);
      assert.deepEqual(scrolled, ["u2"]);
    } finally { await view.unmount(); document.body.innerHTML = ""; }
  });
});
