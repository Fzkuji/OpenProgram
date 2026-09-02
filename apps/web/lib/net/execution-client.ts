import type {
  CommandResult,
  EventCursor,
  ExecutionCommand,
  ExecutionEvent,
  ExecutionSnapshot,
  RevisionChange,
  RevisionDraft,
} from "@/lib/execution-debugger";

export class ExecutionApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ExecutionApiError";
    this.status = status;
    this.code = code;
  }
}

type SnapshotResponse = { snapshot?: ExecutionSnapshot; data?: ExecutionSnapshot };
type EventsResponse = {
  snapshot?: ExecutionSnapshot;
  events?: ExecutionEvent[];
  event_cursor?: EventCursor;
  recovery?: string;
};

export type RunningExecutionList = {
  items: Array<{
    kind?: string;
    execution_id?: string | null;
    snapshot?: ExecutionSnapshot;
    event_cursor?: EventCursor;
  }>;
  now: number;
};

export type CheckpointInspector = {
  checkpoint_id: string;
  execution_id: string;
  revision_id: string;
  parent_checkpoint_id?: string | null;
  source_execution_version?: number;
  status_version?: number;
  safe_point?: Record<string, unknown> | null;
  frontier?: Array<{ step_id: string; status: string; contract_hash?: string }>;
  pending_inputs?: string[];
  effect_receipts?: Array<{ effect_id: string; status: string; kind?: string }>;
};

export type DebuggerStateResponse = {
  type?: string;
  execution_id: string;
  checkpoints?: CheckpointInspector[];
  waits?: import("@/lib/execution-debugger").DurableWait[];
  drafts?: RevisionStateResponse[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: { "Accept": "application/json", ...(init?.headers || {}) },
    });
  } catch (error) {
    throw new ExecutionApiError(0, "network_error", error instanceof Error ? error.message : "Request failed");
  }
  const body = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    const code = typeof body.error === "string" ? body.error : "request_failed";
    throw new ExecutionApiError(response.status, code, code);
  }
  return body as T;
}

export async function getExecutionSnapshot(
  executionId: string,
  signal?: AbortSignal,
): Promise<ExecutionSnapshot> {
  const body = await request<SnapshotResponse>(
    `/api/execution/${encodeURIComponent(executionId)}`,
    { signal, cache: "no-store" },
  );
  const snapshot = body.snapshot || body.data;
  if (!snapshot?.execution_id) throw new ExecutionApiError(200, "invalid_snapshot", "The execution snapshot is invalid.");
  return snapshot;
}

export async function getRunningExecutions(signal?: AbortSignal): Promise<RunningExecutionList> {
  return request<RunningExecutionList>("/api/running", { signal, cache: "no-store" });
}

export async function getExecutionEvents(
  executionId: string,
  afterSequence: number,
  signal?: AbortSignal,
): Promise<EventsResponse> {
  return request<EventsResponse>(
    `/api/execution/${encodeURIComponent(executionId)}/events?after_sequence=${Math.max(0, afterSequence)}`,
    { signal, cache: "no-store" },
  );
}

export async function getExecutionDebuggerState(
  executionId: string,
  signal?: AbortSignal,
): Promise<DebuggerStateResponse> {
  return request<DebuggerStateResponse>(
    `/api/execution/${encodeURIComponent(executionId)}/debugger`,
    { signal, cache: "no-store" },
  );
}

export async function postExecutionCommand(command: ExecutionCommand): Promise<CommandResult> {
  const operation = command.action.slice("execution.".length);
  const pathOperation = operation.startsWith("wait.")
    ? `wait/${operation.slice("wait.".length)}`
    : operation;
  const body = await request<Record<string, unknown>>(`/api/execution/${pathOperation}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  const commandResult = body.command && typeof body.command === "object"
    ? body.command as CommandResult
    : body as CommandResult;
  return {
    ...commandResult,
    execution: (body.execution || body.data && (body.data as Record<string, unknown>).execution) as ExecutionSnapshot | undefined,
  } as CommandResult;
}

export async function postExecutionWait(input: {
  execution_id: string;
  expected_version: number;
  wait_id: string;
  generation: number;
  outcome: "answer" | "decline";
  value?: string;
}): Promise<CommandResult> {
  const action = input.outcome === "answer" ? "answer" : "decline";
  const command: ExecutionCommand = {
    type: "execution.command",
    action: `execution.wait.${action}` as ExecutionCommand["action"],
    command_id: crypto.randomUUID(),
    execution_id: input.execution_id,
    expected_version: input.expected_version,
    payload: {
      wait_id: input.wait_id,
      generation: input.generation,
      ...(input.outcome === "answer" ? { answer: input.value || "" } : { reason: input.value }),
    },
  };
  return postExecutionCommand(command);
}

type RevisionAction =
  | "revision.draft.create"
  | "revision.draft.get"
  | "revision.draft.replace"
  | "revision.draft.discard"
  | "revision.validate"
  | "revision.approve"
  | "revision.publish";

type RevisionStateResponse = {
  draft?: RevisionDraft;
  validation?: RevisionDraft["validation"] | null;
  approval?: RevisionDraft["approval"] | null;
  manifest?: RevisionDraft["manifest"] | null;
  data?: RevisionStateResponse;
};

const REVISION_ROUTES: Partial<Record<RevisionAction, { path: string; method: "POST" | "PUT" }>> = {
  "revision.draft.create": { path: "/api/execution/revision/draft", method: "POST" },
  "revision.draft.replace": { path: "replace", method: "PUT" },
  "revision.draft.discard": { path: "discard", method: "POST" },
  "revision.validate": { path: "validate", method: "POST" },
  "revision.approve": { path: "approve", method: "POST" },
  "revision.publish": { path: "publish", method: "POST" },
};

export function parseRevisionState(body: RevisionStateResponse): RevisionDraft {
  const state = body.draft ? body : body.data?.draft ? body.data : body;
  const draft = state.draft;
  if (!draft?.draft_id) {
    throw new ExecutionApiError(200, "invalid_draft", "The revision draft response is invalid.");
  }
  // The service returns draft, validation, approval, and manifest as one
  // canonical state. Keep that state together for the debugger projection.
  const validation = state.validation as (Record<string, unknown> & {
    report?: Record<string, unknown>;
  }) | null | undefined;
  const approval = state.approval as Record<string, unknown> | null | undefined;
  const manifest = state.manifest as Record<string, unknown> | null | undefined;
  return {
    ...draft,
    validation: validation
      ? {
        validation_id: typeof validation.validation_id === "string" ? validation.validation_id : undefined,
        report_ref: String(validation.validation_id || ""),
        report_hash: String(validation.report_hash || ""),
        reusable_steps: Array.isArray(validation.report?.reusable_steps) ? validation.report.reusable_steps as string[] : [],
        affected_steps: Array.isArray(validation.report?.affected_steps) ? validation.report.affected_steps as string[] : [],
        error_code: typeof validation.report?.error_code === "string" ? validation.report.error_code : null,
      }
      : draft.validation,
    approval: approval
      ? {
        approval_id: typeof approval.approval_id === "string" ? approval.approval_id : undefined,
        approval_ref: String(approval.approval_id || ""),
        policy_version: String(approval.policy_version || ""),
      }
      : draft.approval,
    manifest: manifest
      ? {
        manifest_id: typeof manifest.manifest_id === "string" ? manifest.manifest_id : undefined,
        revision_id: String(manifest.revision_id || ""),
        content_hash: String(manifest.content_hash || ""),
        proof_hash: typeof manifest.proof_hash === "string" ? manifest.proof_hash : undefined,
      }
      : draft.manifest,
  };
}

/** Submit exactly one canonical revision envelope over the REST transport. */
export async function postRevisionDraftCommand(input: {
  execution_id: string;
  action: RevisionAction;
  draft_id?: string;
  expected_draft_version?: number;
  payload?: Record<string, unknown>;
}): Promise<RevisionDraft> {
  const draftId = input.draft_id ? encodeURIComponent(input.draft_id) : "";
  if (input.action === "revision.draft.get") {
    if (!draftId) throw new ExecutionApiError(400, "invalid_command", "A draft id is required.");
    const body = await request<RevisionStateResponse>(
      `/api/execution/${encodeURIComponent(input.execution_id)}/revision/draft/${draftId}`,
      { method: "GET", cache: "no-store" },
    );
    return parseRevisionState(body);
  }
  if (input.action !== "revision.draft.create" && !draftId) {
    throw new ExecutionApiError(400, "invalid_command", "A draft id is required.");
  }
  if (input.action !== "revision.draft.create" && input.expected_draft_version == null) {
    throw new ExecutionApiError(400, "invalid_draft_version", "A draft version is required.");
  }
  const route = REVISION_ROUTES[input.action];
  if (!route) throw new ExecutionApiError(400, "invalid_command", "The revision action is invalid.");
  const path = route.path.startsWith("/")
    ? route.path
    : `/api/execution/revision/draft/${draftId}/${route.path}`;
  const command = {
    type: "revision.draft" as const,
    action: input.action,
    execution_id: input.execution_id,
    ...(input.action === "revision.draft.create" ? {} : { draft_id: input.draft_id }),
    expected_draft_version: input.expected_draft_version ?? 0,
    payload: input.payload ?? {},
  };
  const body = await request<RevisionStateResponse>(path, {
    method: route.method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  return parseRevisionState(body);
}

export function createRevisionDraft(input: {
  execution_id: string;
  source_checkpoint_id: string;
  changes: RevisionChange[];
  frontier_mapping: Array<Record<string, unknown>>;
}): Promise<RevisionDraft> {
  return postRevisionDraftCommand({
    ...input,
    action: "revision.draft.create",
    expected_draft_version: 0,
    payload: {
      source_checkpoint_id: input.source_checkpoint_id,
      changes: input.changes,
      frontier_mapping: input.frontier_mapping,
    },
  });
}

export function getRevisionDraft(executionId: string, draftId: string): Promise<RevisionDraft> {
  return postRevisionDraftCommand({ execution_id: executionId, draft_id: draftId, action: "revision.draft.get" });
}

export function replaceRevisionDraft(input: {
  execution_id: string;
  draft_id: string;
  expected_draft_version: number;
  changes: RevisionChange[];
  frontier_mapping: Array<Record<string, unknown>>;
}): Promise<RevisionDraft> {
  return postRevisionDraftCommand({
    execution_id: input.execution_id,
    draft_id: input.draft_id,
    action: "revision.draft.replace",
    expected_draft_version: input.expected_draft_version,
    payload: { changes: input.changes, frontier_mapping: input.frontier_mapping },
  });
}

export function discardRevisionDraft(input: {
  execution_id: string;
  draft_id: string;
  expected_draft_version: number;
}): Promise<RevisionDraft> {
  return postRevisionDraftCommand({
    ...input,
    action: "revision.draft.discard",
    payload: {},
  });
}

export function validateRevisionDraft(input: {
  execution_id: string;
  draft_id: string;
  expected_draft_version: number;
}): Promise<RevisionDraft> {
  return postRevisionDraftCommand({ ...input, action: "revision.validate", payload: {} });
}

export function approveRevisionDraft(input: {
  execution_id: string;
  draft_id: string;
  expected_draft_version: number;
  validation_id: string;
}): Promise<RevisionDraft> {
  return postRevisionDraftCommand({
    ...input,
    action: "revision.approve",
    payload: { validation_id: input.validation_id },
  });
}

export function publishRevisionDraft(input: {
  execution_id: string;
  draft_id: string;
  expected_draft_version: number;
  validation_id: string;
  approval_id: string;
}): Promise<RevisionDraft> {
  return postRevisionDraftCommand({
    ...input,
    action: "revision.publish",
    payload: { validation_id: input.validation_id, approval_id: input.approval_id },
  });
}

export async function getExecutionAudit(executionId: string, signal?: AbortSignal): Promise<Record<string, unknown>[]> {
  const body = await request<{ events?: Record<string, unknown>[] }>(
    `/api/execution/${encodeURIComponent(executionId)}/audit`,
    { signal, cache: "no-store" },
  );
  return body.events || [];
}
