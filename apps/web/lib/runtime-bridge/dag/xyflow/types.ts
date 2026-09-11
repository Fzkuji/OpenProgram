/** Canvas projection — session graph mapped for @xyflow/react. */

export type DagShape =
  | "diamond"
  | "circle"
  | "triangle"
  | "square"
  | "capsule"
  | "merge_dot";

export type ProjectionEdgeKind =
  | "conv"
  | "fork"
  | "thread"
  | "attach"
  | "ghost";

export interface ProjectionNode {
  id: string;
  x: number;
  y: number;
  shape: DagShape;
  color: string;
  isHead: boolean;
  onHead: boolean;
  inContext: boolean;
  aged: boolean;
  spilled: boolean;
  isGhost: boolean;
  isInert: boolean;
  isSummary: boolean;
  summaryCount: number;
  summaryOpen: boolean;
  threadCount: number;
  threadOpen: boolean;
  status: string;
  isError: boolean;
  isInternal: boolean;
  ownerId: string;
  role: string;
  display: string;
  functionName: string;
  /** Compact capsule range chip: `N · 首→尾` when available. */
  rangeChip: string;
  source: string;
}

export interface ProjectionEdge {
  id: string;
  source: string;
  target: string;
  kind: ProjectionEdgeKind;
  color: string;
  dashed: boolean;
}

export interface CanvasProjection {
  sessionId: string | null;
  headId: string | null;
  nodes: ProjectionNode[];
  edges: ProjectionEdge[];
  empty: boolean;
  skeleton: boolean;
  revision: number;
}

export const EMPTY_PROJECTION: CanvasProjection = {
  sessionId: null,
  headId: null,
  nodes: [],
  edges: [],
  empty: false,
  skeleton: false,
  revision: 0,
};
