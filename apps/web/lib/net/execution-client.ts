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

/**
 * Revision authoring is intentionally a typed boundary even while the server
 * route is being delivered. A missing route is surfaced as an API error in
 * the panel; the UI never reports a local draft as persisted.
 */
export async function postRevisionDraftCommand(input: {
  source_execution_id: string;
  draft_id?: string;
  action: "create" | "write" | "validate" | "approve" | "publish";
  changes?: RevisionChange[];
  expected_version?: number;
}): Promise<RevisionDraft> {
  const path = input.draft_id
    ? `/api/revision/drafts/${encodeURIComponent(input.draft_id)}/${input.action}`
    : "/api/revision/drafts";
  const body = await request<{ draft?: RevisionDraft; data?: RevisionDraft }>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  const draft = body.draft || body.data;
  if (!draft?.draft_id) throw new ExecutionApiError(200, "invalid_draft", "The revision draft response is invalid.");
  return draft;
}

export async function getExecutionAudit(executionId: string, signal?: AbortSignal): Promise<Record<string, unknown>[]> {
  const body = await request<{ events?: Record<string, unknown>[] }>(
    `/api/execution/${encodeURIComponent(executionId)}/audit`,
    { signal, cache: "no-store" },
  );
  return body.events || [];
}
