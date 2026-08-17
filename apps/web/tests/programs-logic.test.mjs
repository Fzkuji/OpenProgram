import assert from "node:assert/strict";
import test from "node:test";

import { buildCallTreeRows } from "../components/programs/programs-logic.ts";

function logic(size) {
  const nodes = Array.from({ length: size }, (_, index) => ({
    id: `n${index}`,
    name: `n${index}`,
    path: `workflows/n${index}`,
    program_kind: "workflow",
    depth: index,
  }));
  const edges = [];
  for (let source = 0; source < size; source += 1) {
    for (let target = source + 1; target < size; target += 1) {
      edges.push({ source: `n${source}`, target: `n${target}` });
    }
  }
  return { root: "n0", nodes, edges };
}

test("dense DAG call trees are bounded and expand each Program once", () => {
  const result = buildCallTreeRows(logic(24), 256);
  const expanded = result.rows.filter((row) => !row.reference);

  assert.equal(result.rows.length, 256);
  assert.equal(result.truncated, true);
  assert.equal(new Set(expanded.map((row) => row.node.id)).size, expanded.length);
});

test("diamond DAG keeps the second shared child as a reference", () => {
  const result = buildCallTreeRows({
    root: "a",
    nodes: ["a", "b", "c", "d"].map((id) => ({
      id, name: id, path: `workflows/${id}`, program_kind: "workflow", depth: 0,
    })),
    edges: [
      { source: "a", target: "b" }, { source: "a", target: "c" },
      { source: "b", target: "d" }, { source: "c", target: "d" },
    ],
  });

  assert.equal(result.rows.filter((row) => row.node.id === "d").length, 2);
  assert.equal(result.rows.filter((row) => row.node.id === "d" && row.reference).length, 1);
});
