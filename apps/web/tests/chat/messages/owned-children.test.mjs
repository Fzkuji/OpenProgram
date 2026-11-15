// Who owns each card and call tree of a reply: the block the server named on
// the child. The evidence old records need is the server's (transcript/children.py).
import assert from "node:assert/strict";
import { test } from "node:test";
import { blockIds, ownedChildren } from "../../../lib/chat/transcript/owned-children.ts";

const tool = (id, name = "agent", input = {}) => ({ type: "tool", tool: name, tool_call_id: id, input: JSON.stringify(input) });
const card = (id, blockId, rowKind = "sub_agent_step") => ({ id, role: "assistant", rowKind, function: "attach", attach: { session_id: "s" }, blockId });
const owners = (blocks, row) => {
  const owned = ownedChildren(blocks, blockIds(blocks), row);
  return { ...owned, byCard: Object.fromEntries([...owned.cards].map(([block, c]) => [c.id, block])) };
};

test("two same-named agent calls in reversed order pair by the blocks the server named", () => {
  const { byCard } = owners([tool("call-1"), tool("call-2")], { attachCards: [card("late", "call-2"), card("early", "call-1")] });
  assert.deepEqual(byCard, { late: "call-2", early: "call-1" });
});

test("call trees and runtime children go on the block they name", () => {
  const blocks = [tool("call-1", "lookup"), tool("gui-1", "gui_agent")];
  const { callRoots, runtimeChildren } = owners(blocks, {
    callRoots: [{ name: "lookup", path: "a", blockId: "call-1" }],
    runtimeChildren: [{ id: "run", role: "assistant", blockId: "gui-1" }],
  });
  assert.equal(callRoots.get("call-1").path, "a");
  assert.equal(runtimeChildren.get("gui-1").id, "run");
});

test("a child naming no block of this reply stays unpaired and is never lost", () => {
  const { byCard, unpairedCards, unpairedRuntimeChildren } = owners([tool("call-1")], {
    attachCards: [card("none", null), card("elsewhere", "call-9"), card("unnamed", undefined)],
    runtimeChildren: [{ id: "run", role: "assistant", blockId: null }],
  });
  assert.deepEqual(byCard, {});
  assert.deepEqual(unpairedCards.map((c) => c.id), ["none", "elsewhere", "unnamed"]);
  assert.deepEqual(unpairedRuntimeChildren.map((c) => c.id), ["run"]);
});

test("no order or name pairing is left: a nameless child never takes a free block", () => {
  const { byCard, callRoots } = owners([tool("call-1"), tool("call-2", "lookup")], {
    attachCards: [card("c", undefined)], callRoots: [{ name: "lookup", path: "a" }],
  });
  assert.deepEqual(byCard, {});
  assert.equal(callRoots.size, 0);
});

test("a card the server sent as an external reference keeps its card and no block takes it", () => {
  const { byCard, references, unpairedCards } = owners([tool("call-1")], {
    attachCards: [card("manual", "call-1", "external_reference")],
  });
  assert.deepEqual(byCard, {});
  assert.deepEqual(unpairedCards, []);
  assert.deepEqual(references.map((c) => c.id), ["manual"]);
});

test("a block keeps its id when the list around it is rebuilt", () => {
  const streaming = [{ type: "text", text: "a" }, { type: "thinking", text: "t" }, tool("call-1"), { type: "text", text: "b" }];
  assert.deepEqual(blockIds(streaming), ["text:0", "thinking:0", "call-1", "text:1"]);
  const split = [{ type: "text", text: "a" }, { type: "text", text: "a2" }, { type: "thinking", text: "t" }, tool("call-1"), { type: "text", text: "b" }];
  assert.deepEqual(blockIds(split), ["text:0", "text:0.1", "thinking:0", "call-1", "text:1"]);
});

test("a tool block with no id and a restart have ids of their own", () => {
  assert.deepEqual(blockIds([{ type: "tool", tool: "x" }, { type: "restart" }, { type: "tool", tool: "y" }, { type: "restart" }]),
    ["tool:0", "restart:0", "tool:1", "restart:1"]);
});

test("a block the server named keeps that id, and the next live block counts past it", () => {
  const blocks = [
    { type: "text", text: "a", id: "text:0" }, { type: "tool", tool: "x", tool_call_id: "gen_t1", id: "tool:1" },
    { type: "text", text: "b" },
  ];
  assert.deepEqual(blockIds(blocks), ["text:0", "tool:1", "text:1"]);
});

test("steps streamed after a mid-turn read number as a cold read of the same reply does", () => {
  const named = (id, block) => ({ ...block, id });
  const thought = (text) => ({ type: "thinking", text });
  const said = (text) => ({ type: "text", text });
  const live = [
    named("thinking:0", thought("t0")), named("call-1", tool("call-1")), thought("t1"), tool("call-2"), thought("t2"), said("end"),
  ];
  const cold = [thought("t0"), tool("call-1"), thought("t1"), tool("call-2"), thought("t2"), said("end")];
  assert.deepEqual(blockIds(live), blockIds(cold));
  assert.deepEqual(blockIds(live), ["thinking:0", "call-1", "thinking:1", "call-2", "thinking:2", "text:0"]);
  assert.equal(new Set(blockIds(live)).size, live.length, "no two steps share a key or an open state");
});

test("a read that ends inside a run continues that run", () => {
  const blocks = [{ type: "text", text: "a", id: "text:0" }, { type: "text", text: "b", id: "text:0.1" }, { type: "text", text: "c" }];
  assert.deepEqual(blockIds(blocks), ["text:0", "text:0.1", "text:0.2"]);
});

test("a repeated call id streamed after a read stays distinct", () => {
  const blocks = [{ ...tool("c"), id: "c" }, { ...tool("c"), id: "c#1" }, tool("c")];
  assert.deepEqual(blockIds(blocks), ["c", "c#1", "c#2"]);
});

test("a joined reply whose parts each number from zero keeps every id distinct", () => {
  const blocks = [
    { type: "text", text: "a", id: "text:0" }, { type: "tool", tool: "x", tool_call_id: "c1", id: "c1" }, { type: "text", text: "b", id: "text:1" },
    { type: "restart" },
    { type: "text", text: "c", id: "text:0" }, { type: "tool", tool: "x", tool_call_id: "c2", id: "c2" }, { type: "text", text: "d", id: "text:1" },
  ];
  const ids = blockIds(blocks);
  assert.equal(new Set(ids).size, blocks.length);
});
