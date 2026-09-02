export const EXECUTION_COMMAND_ACTIONS = [
  "pause",
  "continue",
  "step",
  "steer",
  "fork",
  "retry",
  "cancel",
] as const;

export type ExecutionCommandAction = (typeof EXECUTION_COMMAND_ACTIONS)[number];
export type ExecutionStatus =
  | "queued"
  | "running"
  | "pausing"
  | "paused"
  | "cancelling"
  | "reconciliation_required"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type ExecutionCapabilities = {
  pause: boolean;
  step: boolean;
  steer: boolean;
  fork: boolean;
  retry: boolean;
  safe_point_kinds: string[];
  state_schema_version: number | null;
};

export type EventCursor = {
  execution_id: string;
  next_sequence: number;
  snapshot_status_version: number;
};

export type ExecutionSnapshot = {
  execution_id: string;
  job_id: string;
  run_id: string;
  parent_execution_id: string | null;
  project_id: string;
  session_id: string;
  revision_id: string;
  status: ExecutionStatus;
  status_version: number;
  reason_code: string | null;
  current_attempt_id: string | null;
  owner_lease: Record<string, unknown> | null;
  resource: Record<string, unknown> | null;
  checkpoint_head_id: string | null;
  safe_point: {
    kind?: string;
    step_id?: string;
    phase?: string;
  } | null;
  capabilities: ExecutionCapabilities;
  pending_command_ids: string[];
  active_child_ids: string[];
  effect_summary: Record<string, unknown>;
  terminal_at: number | null;
  updated_at: number;
  event_sequence: number;
};

export type ExecutionCommand = {
  type: "execution.command";
  action: `execution.${ExecutionCommandAction}`;
  command_id: string;
  execution_id: string;
  expected_version: number;
  payload: Record<string, unknown>;
};

export type CommandResult = {
  command_id: string;
  status: "accepted" | "applying" | "applied" | "rejected";
  rejection_code?: string | null;
  execution?: ExecutionSnapshot;
};

export type ExecutionEvent = {
  sequence: number;
  status_version: number;
  execution: ExecutionSnapshot;
};

export type RevisionChange = {
  kind:
    | "workflow"
    | "prompt"
    | "tool_contract"
    | "model_policy"
    | "output_schema"
    | "program_artifact";
  target: string;
  after_ref: string;
  rationale: string;
};

export type RevisionDraft = {
  draft_id: string;
  project_id: string;
  source_execution_id: string;
  base_revision_id: string;
  base_revision_hash: string;
  source_checkpoint_id: string;
  draft_version: number;
  changes: RevisionChange[];
  status: "draft" | "validated" | "approved" | "published" | "rejected";
  validation?: {
    report_ref: string;
    report_hash: string;
    reusable_steps: string[];
    affected_steps: string[];
    error_code?: string | null;
  };
  approval?: { approval_ref: string; policy_version: string };
  manifest?: { revision_id: string; content_hash: string };
};

export type DurableWait = {
  wait_id: string;
  execution_id: string;
  kind: "question" | "approval";
  request_ref: string;
  request_hash: string;
  claim_generation: number;
  status: "open" | "claimed" | "resolved" | "declined" | "expired" | "cancelled";
  expires_at: number | null;
};

export type CursorHealth = "healthy" | "reconnecting" | "gap" | "stale";

export function canExecuteAction(
  snapshot: ExecutionSnapshot,
  action: ExecutionCommandAction,
): boolean {
  if (action === "cancel") {
    return !["completed", "failed", "cancelled", "interrupted"].includes(snapshot.status);
  }
  if (action === "continue") {
    return snapshot.status === "paused"
      && Boolean(snapshot.checkpoint_head_id || snapshot.resource?.initial_handoff);
  }
  if (action === "pause") return snapshot.status === "queued" || snapshot.status === "running";
  if (action === "step") return snapshot.status === "paused" && snapshot.capabilities.step;
  if (action === "steer") return snapshot.status === "paused" && snapshot.capabilities.steer;
  if (action === "fork") return ["paused", "completed", "failed", "interrupted"].includes(snapshot.status)
    && snapshot.capabilities.fork;
  return ["failed", "interrupted"].includes(snapshot.status) && snapshot.capabilities.retry;
}

export function availableExecutionActions(snapshot: ExecutionSnapshot): ExecutionCommandAction[] {
  return EXECUTION_COMMAND_ACTIONS.filter((action) => canExecuteAction(snapshot, action));
}

export function buildExecutionCommand(
  snapshot: ExecutionSnapshot,
  action: ExecutionCommandAction,
  commandId: string,
  payload: Record<string, unknown> = {},
): ExecutionCommand {
  if (!canExecuteAction(snapshot, action)) {
    throw new Error(`action ${action} is unavailable for ${snapshot.status}`);
  }
  return {
    type: "execution.command",
    action: `execution.${action}`,
    command_id: commandId,
    execution_id: snapshot.execution_id,
    expected_version: snapshot.status_version,
    payload,
  };
}

export function cursorHealth(input: {
  expected: number;
  received: number;
  connected: boolean;
  snapshotVersion?: number;
  eventVersion?: number;
}): CursorHealth {
  if (!input.connected) return "reconnecting";
  if (input.received !== input.expected) return "gap";
  if (input.snapshotVersion != null && input.eventVersion != null
    && input.eventVersion < input.snapshotVersion) return "stale";
  return "healthy";
}

export function reduceExecutionEvent(
  snapshot: ExecutionSnapshot,
  event: ExecutionEvent,
): { kind: "accepted"; snapshot: ExecutionSnapshot } | { kind: "gap"; expected: number; received: number } | { kind: "stale"; snapshotVersion: number; eventVersion: number } {
  const expected = snapshot.event_sequence + 1;
  if (event.sequence !== expected) return { kind: "gap", expected, received: event.sequence };
  if (event.status_version < snapshot.status_version) {
    return { kind: "stale", snapshotVersion: snapshot.status_version, eventVersion: event.status_version };
  }
  return { kind: "accepted", snapshot: event.execution };
}

export function newCommandId(): string {
  return typeof crypto?.randomUUID === "function"
    ? crypto.randomUUID()
    : `cmd-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
