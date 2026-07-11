/**
 * Execution call-tree node — the shape the backend serializes for
 * context_tree / callRoots. Rendered recursively by TreeStep
 * (execution-strip.tsx) in chat, and by the right-dock detail panel.
 */
export interface TNode {
  path?: string;
  name?: string;
  status?: string;
  node_type?: string;
  params?: Record<string, unknown>;
  output?: unknown;
  raw_reply?: string;
  duration_ms?: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  children?: TNode[];
}
