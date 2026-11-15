/** execution_stream.v1 types — mirrors design §5. */

export const EXECUTION_STREAM_SCHEMA_VERSION = 1;

export type StreamOp =
  | "attempt_started"
  | "block_started"
  | "block_delta"
  | "block_finished"
  | "attempt_finished"
  | "snapshot"
  | "node_finished"
  | "sync_required";

export type BlockKind =
  | "text"
  | "reasoning_summary"
  | "tool_ref"
  | "tool_arguments"
  | "parallel_group"
  | "refusal"
  | "unsupported";

export type NodePhase =
  | "created"
  | "running"
  | "retry_wait"
  | "validating"
  | "finalizing"
  | "completed"
  | "cancelling"
  | "cancelled"
  | "failed"
  | "paused"
  | "reconciliation_required"
  | "syncing";

export interface StreamBlock {
  block_id: string;
  message_id: string;
  block_index: number;
  kind: BlockKind | string;
  visibility?: string;
  retention?: string;
  content: string;
  status: "running" | "finished" | string;
  finish_reason?: string;
  omitted_by_policy?: boolean;
  truncated?: boolean;
  /** tool_ref: stable tool call id */
  tool_call_id?: string;
  /** qualified occurrence id; raw ids may repeat across provider turns */
  occurrence_id?: string;
  /** tool_ref: DAG tool node path/id */
  ref_node_id?: string;
  tool_name?: string;
  group_id?: string | null;
}

export interface StreamAttempt {
  attempt_id: string;
  attempt_index: number;
  reason?: string;
  status: string;
  validation?: string;
  provider?: string;
  model?: string;
  parent_attempt_id?: string | null;
  message_id?: string;
  usage?: Record<string, unknown> | null;
  blocks: StreamBlock[];
}

export interface NodeStreamState {
  session_id: string;
  execution_id: string;
  node_id: string;
  generation: number;
  revision: number;
  display_msg_id: string;
  phase: NodePhase | string;
  current_attempt_id?: string | null;
  selected_attempt_id?: string | null;
  attempts: StreamAttempt[];
  preview_text: string;
  preview_reasoning: string;
  syncing?: boolean;
  terminal?: boolean;
  truncated?: boolean;
  durability?: string;
  last_error?: string;
}

export interface ExecutionStreamEvent {
  type: "execution_stream";
  schema_version: number;
  session_id: string;
  execution_id: string;
  node_id: string;
  generation: number;
  display_msg_id?: string;
  op: StreamOp | string;
  base_revision?: number;
  revision?: number;
  attempt_id?: string;
  attempt_index?: number;
  reason?: string;
  provider?: string;
  model?: string;
  parent_attempt_id?: string | null;
  phase?: string;
  message_id?: string;
  block_id?: string;
  block_index?: number;
  kind?: string;
  tool_call_id?: string;
  occurrence_id?: string;
  ref_node_id?: string;
  tool_name?: string;
  group_id?: string | null;
  visibility?: string;
  retention?: string;
  delta?: string;
  finish_reason?: string;
  status?: string;
  validation?: string;
  usage?: Record<string, unknown>;
  error_summary?: string;
  checkpoint_revision?: number;
  terminal_revision?: number;
  reason_code?: string;
  durability?: string;
  snapshot?: SnapshotPayload;
}

export interface SnapshotPayload {
  revision: number;
  checkpoint_revision?: number;
  generation?: number;
  phase?: string;
  durability?: string;
  current_attempt_id?: string | null;
  selected_attempt_id?: string | null;
  result?: unknown;
  attempts?: StreamAttempt[];
  preview_text?: string;
  preview_reasoning?: string;
  recovered_from_checkpoint?: boolean;
  source_checkpoint?: { generation: number; revision: number } | null;
}

export type ApplyResult =
  | { ok: true; state: NodeStreamState; requestSnapshot?: boolean }
  | { ok: false; state: NodeStreamState; reason: string; requestSnapshot?: boolean };
