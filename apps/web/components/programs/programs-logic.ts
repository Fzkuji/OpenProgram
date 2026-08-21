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

function longestPathDepth(
  root: string,
  nodes: LogicNode[],
  edges: Array<{ source: string; target: string }>,
) {
  const depth = new Map<string, number>([[root, 0]]);
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const edge of edges) {
      const sourceDepth = depth.get(edge.source);
      if (sourceDepth === undefined) continue;
      const next = sourceDepth + 1;
      const current = depth.get(edge.target);
      if (current === undefined || current < next) {
        depth.set(edge.target, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  for (const node of nodes) {
    if (!depth.has(node.id)) depth.set(node.id, Math.max(0, node.depth));
  }
  return depth;
}

function sortByBarycenter(
  layer: string[],
  related: Map<string, string[]>,
  other: string[],
) {
  const index = new Map(other.map((id, position) => [id, position]));
  return [...layer].sort((left, right) => {
    const score = (id: string) => {
      const neighbors = (related.get(id) ?? [])
        .map((neighbor) => index.get(neighbor))
        .filter((position): position is number => position !== undefined);
      if (!neighbors.length) return layer.indexOf(id);
      return neighbors.reduce((sum, position) => sum + position, 0) / neighbors.length;
    };
    return score(left) - score(right) || layer.indexOf(left) - layer.indexOf(right);
  });
}

function spreadRows(desired: number[], minGap: number, minY: number, maxY: number) {
  const rows = desired.map((value, index) => ({ value, index }));
  rows.sort((left, right) => left.value - right.value || left.index - right.index);
  for (let index = 1; index < rows.length; index += 1) {
    rows[index].value = Math.max(rows[index].value, rows[index - 1].value + minGap);
  }
  const overflow = rows.length ? rows[rows.length - 1].value - maxY : 0;
  if (overflow > 0) {
    for (const row of rows) row.value -= overflow;
  }
  if (rows.length && rows[0].value < minY) {
    const shift = minY - rows[0].value;
    for (const row of rows) row.value += shift;
  }
  const result = new Array<number>(desired.length);
  for (const row of rows) result[row.index] = row.value;
  return result;
}

export function buildGraphLayout(logic: LogicResponse): GraphLayout {
  const children = new Map<string, string[]>();
  const parents = new Map<string, string[]>();
  for (const edge of logic.edges) {
    children.set(edge.source, [...(children.get(edge.source) ?? []), edge.target]);
    parents.set(edge.target, [...(parents.get(edge.target) ?? []), edge.source]);
  }

  const depth = longestPathDepth(logic.root, logic.nodes, logic.edges);
  const byId = new Map(logic.nodes.map((node) => [node.id, node]));
  const layerIds = new Map<number, string[]>();
  for (const node of logic.nodes) {
    const level = depth.get(node.id) ?? 0;
    layerIds.set(level, [...(layerIds.get(level) ?? []), node.id]);
  }
  const levels = [...layerIds.keys()].sort((left, right) => left - right);
  const layers = levels.map((level) => layerIds.get(level) ?? []);

  for (let sweep = 0; sweep < 3; sweep += 1) {
    for (let index = 1; index < layers.length; index += 1) {
      layers[index] = sortByBarycenter(layers[index], parents, layers[index - 1]);
    }
    for (let index = layers.length - 2; index >= 1; index -= 1) {
      layers[index] = sortByBarycenter(layers[index], children, layers[index + 1]);
    }
  }

  const nodeWidth = GRAPH_NODE_WIDTH;
  const nodeHeight = GRAPH_NODE_HEIGHT;
  const horizontalGap = 24;
  const verticalGap = 28;
  const padding = 20;
  const maxLayerSize = Math.max(1, ...layers.map((layer) => layer.length));
  const height = padding * 2 + maxLayerSize * nodeHeight + (maxLayerSize - 1) * verticalGap;
  const maxDepth = Math.max(0, ...levels);
  const width = padding * 2 + (maxDepth + 1) * nodeWidth + maxDepth * horizontalGap;
  const minY = padding;
  const maxY = height - padding - nodeHeight;
  const minGap = nodeHeight + verticalGap;
  const coordinates = new Map<string, { x: number; y: number }>();

  layers.forEach((layer, index) => {
    const x = padding + (levels[index] ?? index) * (nodeWidth + horizontalGap);
    const layerHeight = layer.length * nodeHeight + Math.max(0, layer.length - 1) * verticalGap;
    const startY = (height - layerHeight) / 2;
    layer.forEach((id, position) => {
      coordinates.set(id, { x, y: startY + position * minGap });
    });
  });

  for (let pass = 0; pass < 2; pass += 1) {
    for (let index = 1; index < layers.length; index += 1) {
      const layer = layers[index];
      const desired = layer.map((id, position) => {
        const related = (parents.get(id) ?? [])
          .map((parent) => coordinates.get(parent)?.y)
          .filter((value): value is number => value !== undefined);
        return related.length
          ? related.reduce((sum, value) => sum + value, 0) / related.length
          : coordinates.get(id)?.y ?? 0;
      });
      const rows = spreadRows(desired, minGap, minY, maxY);
      layer.forEach((id, position) => {
        const current = coordinates.get(id);
        if (current) current.y = rows[position];
      });
    }
  }

  const positioned: GraphLayoutNode[] = [];
  for (const node of logic.nodes) {
    const point = coordinates.get(node.id);
    if (!point) continue;
    positioned.push({ ...node, x: point.x, y: point.y });
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
