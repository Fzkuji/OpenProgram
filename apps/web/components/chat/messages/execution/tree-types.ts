/**
 * Execution call-tree node — the shape the backend serializes for
 * context_tree / callRoots. Rendered recursively by TreeStep
 * (execution-strip.tsx) in chat, and by the right-dock detail panel.
 */
import type { StreamAttempt } from "@/lib/execution-stream/types";

export interface TNode {
  path?: string;
  name?: string;
  /** The model's tool call this tree ran for, when its record names one. */
  tool_call_id?: string;
  status?: string;
  node_type?: string;
  /** Visibility policy for descendants in nested content. */
  expose?: "full" | "llm" | "io" | "hidden" | string;
  params?: Record<string, unknown>;
  output?: unknown;
  raw_reply?: string;
  duration_ms?: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  children?: TNode[];
  stream_preview?: string;
  stream_reasoning?: string;
  stream_revision?: number;
  stream_generation?: number;
  stream_phase?: string;
  /** Durable execution_stream snapshot used after owner loss/reload. */
  stream_attempts?: StreamAttempt[];
  /** Legacy ordered block projection kept for pre-v1 records. */
  stream_blocks?: Array<Record<string, unknown>>;
  stream_snapshot?: Record<string, unknown>;
}
