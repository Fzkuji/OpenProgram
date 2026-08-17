export type ProgramKind = "vanilla_function" | "agentic_function" | "workflow" | "application";

export type LogicNode = {
  id: string;
  name: string;
  path: string;
  program_kind: ProgramKind;
  depth: number;
};

export type LogicResponse = {
  root: string;
  nodes: LogicNode[];
  edges: Array<{ source: string; target: string }>;
  analysis_complete?: boolean;
  analysis_warnings?: string[];
};

export type CallTreeRow = {
  key: string;
  node: LogicNode;
  depth: number;
  reference: boolean;
  cycle: boolean;
};

export function buildCallTreeRows(logic: LogicResponse, limit = 256) {
  const nodes = new Map(logic.nodes.map((node) => [node.id, node]));
  const adjacency = new Map<string, string[]>();
  for (const edge of logic.edges) {
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
  }
  const rows: CallTreeRow[] = [];
  const expanded = new Set<string>();
  let truncated = false;

  function visit(id: string, depth: number, ancestry: Set<string>, key: string) {
    if (rows.length >= limit) {
      truncated = true;
      return;
    }
    const node = nodes.get(id);
    if (!node) return;
    const cycle = ancestry.has(id);
    const reference = cycle || expanded.has(id);
    rows.push({ key, node, depth, reference, cycle });
    if (reference) return;
    expanded.add(id);
    const nextAncestry = new Set(ancestry).add(id);
    for (const [index, target] of (adjacency.get(id) ?? []).entries()) {
      visit(target, depth + 1, nextAncestry, `${key}/${index}:${target}`);
    }
  }

  visit(logic.root, 0, new Set(), logic.root);
  return { rows, truncated };
}
