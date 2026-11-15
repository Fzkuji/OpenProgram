// A read sends each tool body once, on its block; the store's rows have both.
import assert from "node:assert/strict";
import { test } from "node:test";
import { rowsOfTurns } from "../../../lib/chat/transcript/turn-rows.ts";

const call = (id, input, result) => ({ type: "tool", tool: "x", tool_call_id: id, id, input, result });
const flat = (id) => ({ id, tool: "x", status: "done" });

test("a tool's flat entry gets its input and result from its block", () => {
  const [row] = rowsOfTurns([{ id: "t", rows: [{ id: "r", blocks: [call("c1", "{\"a\":1}", "out")], tools: [flat("c1")] }] }]);
  assert.deepEqual(row.tools, [{ id: "c1", tool: "x", status: "done", input: "{\"a\":1}", result: "out" }]);
});

test("a call id stored twice pairs by position, and a call with no flat entry is skipped", () => {
  const blocks = [call("c", "1", "a"), { ...call("agent", "2", "b"), tool: "gui_agent" }, call("c", "3", "c")];
  const [row] = rowsOfTurns([{ id: "t", rows: [{ id: "r", blocks, tools: [flat("c"), flat("c")] }] }]);
  assert.deepEqual(row.tools.map((tool) => [tool.input, tool.result]), [["1", "a"], ["3", "c"]]);
});

test("a card inside a reply is filled too, and a row with no tools passes through", () => {
  const card = { id: "card", blocks: [call("c2", "in", "out")], tools: [flat("c2")] };
  const plain = { id: "plain", content: "hello" };
  const [reply, other] = rowsOfTurns([{ id: "t", rows: [{ id: "r", attachCards: [card] }, plain] }]);
  assert.equal(reply.attachCards[0].tools[0].result, "out");
  assert.equal(other, plain);
});

test("the turns stay as sent, so a history window counts the bytes that travelled", () => {
  const turns = [{ id: "t", rows: [{ id: "r", blocks: [call("c1", "in", "out")], tools: [flat("c1")] }] }];
  const before = JSON.stringify(turns);
  rowsOfTurns(turns);
  assert.equal(JSON.stringify(turns), before);
});
