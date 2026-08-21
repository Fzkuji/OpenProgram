export type ProgramKind = "vanilla_function" | "agentic_function" | "workflow" | "application" | "runtime_primitive";

export type LogicNode = {
  id: string;
  name: string;
  path: string;
  program_kind: ProgramKind | null;
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
  ancestorContinuations: boolean[];
  isLast: boolean;
};

export type GraphLayoutNode = LogicNode & { x: number; y: number };
export type GraphLayout = {
  nodes: GraphLayoutNode[];
  edges: Array<{ source: GraphLayoutNode; target: GraphLayoutNode }>;
  width: number;
  height: number;
};

export const GRAPH_NODE_WIDTH = 164;
export const GRAPH_NODE_HEIGHT = 64;

export function buildCallTreeRows(logic: LogicResponse, limit = 256) {
  const nodes = new Map(logic.nodes.map((node) => [node.id, node]));
  const adjacency = new Map<string, string[]>();
  for (const edge of logic.edges) {
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
  }
  const rows: CallTreeRow[] = [];
  const expanded = new Set<string>();
  let truncated = false;

  function visit(
    id: string,
    depth: number,
    ancestry: Set<string>,
    key: string,
    ancestorContinuations: boolean[],
    isLast: boolean,
  ) {
    if (rows.length >= limit) {
      truncated = true;
      return;
    }
    const node = nodes.get(id);
    if (!node) return;
    const cycle = ancestry.has(id);
    const reference = cycle || expanded.has(id);
    rows.push({ key, node, depth, reference, cycle, ancestorContinuations, isLast });
    if (reference) return;
    expanded.add(id);
    const nextAncestry = new Set(ancestry).add(id);
    const targets = adjacency.get(id) ?? [];
    for (const [index, target] of targets.entries()) {
      visit(
        target,
        depth + 1,
        nextAncestry,
        `${key}/${index}:${target}`,
        depth === 0 ? [] : [...ancestorContinuations, !isLast],
        index === targets.length - 1,
      );
    }
  }

  visit(logic.root, 0, new Set(), logic.root, [], true);
  return { rows, truncated };
}

export function buildGraphLayout(logic: LogicResponse): GraphLayout {
  const adjacency = new Map<string, string[]>();
  for (const edge of logic.edges) {
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
  }

  const depth = new Map<string, number>([[logic.root, 0]]);
  const queue = [logic.root];
  while (queue.length) {
    const source = queue.shift()!;
    const nextDepth = (depth.get(source) ?? 0) + 1;
    for (const target of adjacency.get(source) ?? []) {
      if (!depth.has(target)) {
        depth.set(target, nextDepth);
        queue.push(target);
      }
    }
  }

  const layers = new Map<number, LogicNode[]>();
  for (const node of logic.nodes) {
    const level = depth.get(node.id) ?? Math.max(0, node.depth);
    layers.set(level, [...(layers.get(level) ?? []), node]);
  }
  const nodeWidth = GRAPH_NODE_WIDTH;
  const nodeHeight = GRAPH_NODE_HEIGHT;
  const horizontalGap = 24;
  const verticalGap = 24;
  const padding = 20;
  const maxLayerSize = Math.max(1, ...[...layers.values()].map((layer) => layer.length));
  const height = padding * 2 + maxLayerSize * nodeHeight + (maxLayerSize - 1) * verticalGap;
  const maxDepth = Math.max(0, ...layers.keys());
  const width = padding * 2 + (maxDepth + 1) * nodeWidth + maxDepth * horizontalGap;
  const positioned: GraphLayoutNode[] = [];
  for (const [level, layer] of [...layers.entries()].sort(([a], [b]) => a - b)) {
    const layerHeight = layer.length * nodeHeight + Math.max(0, layer.length - 1) * verticalGap;
    const startY = (height - layerHeight) / 2;
    layer.forEach((node, index) => positioned.push({
      ...node,
      x: padding + level * (nodeWidth + horizontalGap),
      y: startY + index * (nodeHeight + verticalGap),
    }));
  }
  const positionedById = new Map(positioned.map((node) => [node.id, node]));
  return {
    nodes: positioned,
    edges: logic.edges.flatMap((edge) => {
      const source = positionedById.get(edge.source);
      const target = positionedById.get(edge.target);
      return source && target ? [{ source, target }] : [];
    }),
    width,
    height,
  };
}
