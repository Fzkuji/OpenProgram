import assert from "node:assert/strict";
import { describe, test } from "node:test";

const { groupTools, TOOL_GROUPS } = await import("../../components/agents/tool-groups.ts");

describe("Agent tool groups", () => {
  test("MCP tools group by server, alphabetically", () => {
    const rows = [
      { name: "linear__get_issue", group: "connected", source: "mcp", server: "linear" },
      { name: "linear__save_issue", group: "connected", source: "mcp", server: "linear" },
      { name: "drawio__set_page", group: "connected", source: "mcp", server: "drawio" },
    ];
    assert.deepEqual(
      groupTools(rows, "server").map((group) => [group.name, group.items.map((tool) => tool.name)]),
      [["drawio", ["drawio__set_page"]], ["linear", ["linear__get_issue", "linear__save_issue"]]],
    );
  });

  test("an MCP tool without a server lands in the MCP group", () => {
    assert.deepEqual(groupTools([{ name: "x", source: "mcp" }], "server").map((group) => group.name), ["MCP"]);
  });

  test("built-in tools group by category in the fixed group order, unknown groups last", () => {
    const rows = [
      { name: "todo", group: "planning" },
      { name: "mystery", group: "made-up" },
      { name: "read", group: "file" },
      { name: "loose" },
      { name: "fetch", group: "web" },
    ];
    assert.deepEqual(groupTools(rows).map((group) => group.name), ["file", "web", "planning", "other", "made-up"]);
    assert.equal(TOOL_GROUPS[0][0], "file");
    assert.equal(TOOL_GROUPS.at(-1)[0], "other");
  });
});
