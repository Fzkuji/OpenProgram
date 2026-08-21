import assert from "node:assert/strict";
import test from "node:test";

import { buildCallTreeRows, buildGraphLayout } from "../components/programs/programs-logic.ts";

function logic(size) {
  const nodes = Array.from({ length: size }, (_, index) => ({
    id: `n${index}`,
    name: `n${index}`,
    path: `workflow/n${index}`,
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
      id, name: id, path: `workflow/${id}`, program_kind: "workflow", depth: 0,
    })),
    edges: [
      { source: "a", target: "b" }, { source: "a", target: "c" },
      { source: "b", target: "d" }, { source: "c", target: "d" },
    ],
  });

  assert.equal(result.rows.filter((row) => row.node.id === "d").length, 2);
  assert.equal(result.rows.filter((row) => row.node.id === "d" && row.reference).length, 1);
});

test("call tree rows retain ancestor continuation lines", () => {
  const result = buildCallTreeRows({
    root: "a",
    nodes: ["a", "b", "c", "d"].map((id) => ({
      id, name: id, path: `workflow/${id}`, program_kind: "workflow", depth: 0,
    })),
    edges: [
      { source: "a", target: "b" }, { source: "a", target: "c" },
      { source: "b", target: "d" },
    ],
  });

  const b = result.rows.find((row) => row.node.id === "b");
  const c = result.rows.find((row) => row.node.id === "c");
  const d = result.rows.find((row) => row.node.id === "d");
  assert.equal(b.isLast, false);
  assert.equal(c.isLast, true);
  assert.deepEqual(d.ancestorContinuations, [true]);
});

test("graph layout renders each Program once in dependency layers", () => {
  const layout = buildGraphLayout({
    root: "a",
    nodes: ["a", "b", "c", "d"].map((id) => ({
      id, name: id, path: `workflow/${id}`, program_kind: "workflow", depth: 0,
    })),
    edges: [
      { source: "a", target: "b" }, { source: "a", target: "c" },
      { source: "b", target: "d" }, { source: "c", target: "d" },
    ],
  });
  const byId = new Map(layout.nodes.map((node) => [node.id, node]));

  assert.equal(layout.nodes.length, 4);
  assert.equal(new Set(layout.nodes.map((node) => node.id)).size, 4);
  assert.equal(layout.edges.length, 4);
  assert.ok(byId.get("a").x < byId.get("b").x);
  assert.equal(byId.get("b").x, byId.get("c").x);
  assert.ok(byId.get("b").x < byId.get("d").x);
});

test("a four-level graph fits the standard Programs detail width", () => {
  const nodes = ["workflow", "goal", "agent", "llm"].map((id, depth) => ({
    id, name: id, path: `agentic_programming/${id}`, program_kind: "agentic_function", depth,
  }));
  const layout = buildGraphLayout({
    root: "workflow",
    nodes,
    edges: [
      { source: "workflow", target: "goal" },
      { source: "goal", target: "agent" },
      { source: "agent", target: "llm" },
    ],
  });

  assert.ok(layout.width <= 768);
  assert.equal(layout.nodes.at(-1).x + 164, layout.width - 20);
});
