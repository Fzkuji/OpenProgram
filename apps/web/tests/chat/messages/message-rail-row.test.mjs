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
const { useTaskStore } = await import("../../../lib/chat/transcript/task-blocks.ts");
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

test("a turn's tasks ride the packed row, and a turn with none carries no field", () => {
  const tasks = [
    { id: "T1", title: "盘点设置页控件", anchor: "a1", steps: 3 },
    { id: "T2", title: "Migrate to Nova", anchor: "a1", from: 6, to: 9 },
  ];
  const row = { ...rows[0], tasks };
  assert.deepEqual(unpackRailRow(packRailRow(row)), row);
  assert.equal("tasks" in unpackRailRow(packRailRow({ ...rows[0], tasks: [] })), false);
  assert.notEqual(packRailRow(row), packRailRow(rows[0]));
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

  test("a turn whose reply declared a task has a longer tick, and its card lists the task", async () => {
    seed(["u1", "u2", "u3"], ["u3"]);
    useTaskStore.getState().setAll("s1", [
      { anchor_id: "u2_reply", plan_id: "T1", scope: "", title: "Fold the agent list", state: "done", advanced: true,
        todos: [{ id: "1", subject: "look", status: "completed" }, { id: "2", subject: "edit", status: "completed" }] },
      { anchor_id: "u2_reply", plan_id: "T9", scope: "agent:reviewer", title: "Its own task", state: "done", todos: [] },
    ]);
    const view = await render(createElement(MessageRail, {}));
    try {
      const ticks = [...view.container.querySelectorAll(".msg-rail-dash")];
      assert.deepEqual(ticks.map((tick) => tick.style.width), ["9px", "14px", "18px"]);
      await view.act(() => ticks[1].dispatchEvent(new Event("focusin", { bubbles: true })));
      const entries = [...view.container.querySelectorAll(".msg-rail-card-task")];
      assert.deepEqual(entries.map((entry) => entry.textContent), ["Fold the agent list2 steps"]);
      assert.ok(view.container.querySelector(".msg-rail-card").classList.contains("has-tasks"));
    } finally { await view.unmount(); useTaskStore.getState().setAll("s1", []); }
  });

  test("a task reply's card shows the answer after its last call, not text said inside a step", async () => {
    seed(["u1", "u2"]);
    useSessionStore.setState((state) => ({ rowsByView: { s1: {
      ...state.rowsByView.s1,
      u1_reply: { ...state.rowsByView.s1.u1_reply, rowKind: "assistant" },
      u2_reply: { ...state.rowsByView.s1.u2_reply, rowKind: "assistant", blocks: [
        { type: "text", text: "opening" },
        { type: "tool", tool: "task_start", tool_call_id: "c1" },
        { type: "text", text: "said inside a step" },
        { type: "tool", tool: "bash", tool_call_id: "c2" },
        { type: "text", text: "the answer" },
      ] },
    } } }));
    useTaskStore.getState().setAll("s1", [
      { anchor_id: "u2_reply", plan_id: "T1", scope: "", title: "Fold the agent list", state: "done", advanced: true,
        todos: [{ id: "1", subject: "look", status: "completed" }] },
    ]);
    const view = await render(createElement(MessageRail, {}));
    try {
      const ticks = [...view.container.querySelectorAll(".msg-rail-dash")];
      await view.act(() => ticks[1].dispatchEvent(new Event("focusin", { bubbles: true })));
      assert.equal(view.container.querySelector(".msg-rail-card-reply").textContent.trim(), "the answer");
      await view.act(() => ticks[0].dispatchEvent(new Event("focusin", { bubbles: true })));
      assert.equal(view.container.querySelector(".msg-rail-card-reply").textContent.trim(), "answer u1");
    } finally { await view.unmount(); useTaskStore.getState().setAll("s1", []); }
  });

  test("one message draws no rail, unless its replies hold two task blocks, as a Goal's do", async () => {
    seed(["u1"]);
    const reply = { id: "u1_next", role: "assistant", content: "next turn", status: "done" };
    useSessionStore.setState((state) => ({
      rowsByView: { s1: { ...state.rowsByView.s1, u1_next: reply } }, messageOrder: { s1: ["u1", "u1_reply", "u1_next"] },
    }));
    const segment = (anchor, earlier) => ({
      anchor_id: anchor, plan_id: "T1", scope: "", title: "Migrate to Nova", state: "running", advanced: true, earlier,
      todos: [{ id: "1", subject: "a", status: "completed" }, { id: "2", subject: "b", status: "in_progress" }],
    });
    useTaskStore.getState().setAll("s1", [segment("u1_reply", [])]);
    let view = await render(createElement(MessageRail, {}));
    try {
      assert.equal(view.container.querySelector(".msg-rail-dash"), null);
    } finally { await view.unmount(); }
    useTaskStore.getState().setAll("s1", [segment("u1_reply", []), segment("u1_next", ["1"])]);
    view = await render(createElement(MessageRail, {}));
    try {
      const ticks = [...view.container.querySelectorAll(".msg-rail-dash")];
      assert.equal(ticks.length, 1);
      await view.act(() => ticks[0].dispatchEvent(new Event("focusin", { bubbles: true })));
      assert.equal(view.container.querySelectorAll(".msg-rail-card-task").length, 2);
    } finally { await view.unmount(); useTaskStore.getState().setAll("s1", []); }
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
