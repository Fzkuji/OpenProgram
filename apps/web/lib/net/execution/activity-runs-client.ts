/** Read-only endpoints behind the Activity pages: runs and what happened inside one. */
import { jsonFetch } from "../fetch-client";

export type ActivityAskedBy = "you" | "check-in" | "schedule" | "none";
export type ActivityTaskOutcome = "quiet" | "result" | "failed" | "skipped" | "missed";

export interface ActivityRun {
  /** Assistant message id, or the execution id when the run has no reply yet. */
  id: string;
  message_id: string | null;
  user_message_id: string | null;
  execution_id: string | null;
  /** Node id starts with `report_`. */
  is_report: boolean;
  asked_by: ActivityAskedBy;
  /** First 200 characters of what was asked. */
  asked_preview: string;
  /** Raw reply text, tags included, at most 4000 characters. */
  reply: string;
  reply_truncated: boolean;
  started_at: number | null;
  /** null means no end was saved or the run is still going. */
  ended_at: number | null;
  /** Raw ExecutionStatus value, null for a message-only run. */
  execution_status: string | null;
  /** `metadata.status` of the reply node. */
  message_status: string | null;
  reason_code: string | null;
  /** Task pages only. */
  task_outcome: ActivityTaskOutcome | null;
  task_text: string | null;
  /** Steps plus sub-agents; 0 means the row has nothing to open. */
  event_count: number;
  /** Changes whenever this run's events could have changed. */
  revision: string;
  /** The time chart only: a short form of every event, without the question asked. */
  steps?: ActivityRunEvent[];
  topic_id?: string | null;
  topic_title?: string | null;
  /** Which topic came first in the conversation; it picks the topic's colour. */
  topic_order?: number | null;
}

export interface ActivityRunPage {
  runs: ActivityRun[];
  offset: number;
  total: number;
  /** The time chart only: a pass over the conversation's topics is under way. */
  topics_sorting?: boolean;
}

export interface ActivityAskedEvent {
  kind: "asked"; at: number; by: ActivityAskedBy; text: string; truncated: boolean;
}
export interface ActivityStepEvent {
  kind: "step"; at: number; node_id: string; tool_call_id: string | null;
  name: string; input: unknown; input_truncated: boolean;
  result: string | null; result_truncated: boolean; failed: boolean;
}
export interface ActivityRepliedEvent {
  kind: "replied"; at: number; text: string; truncated: boolean;
}
export interface ActivityAttemptEvent {
  kind: "attempt"; at: number; number: number; ended_at: number | null;
  status: string; outcome: string | null; reason_code: string | null;
  failure_text: string | null; checkpoint_id: string | null;
}
export interface ActivitySubAgentEvent {
  kind: "subagent"; at: number; ended_at: number | null;
  /** Equals the sub-agent's execution id; null when it never started. */
  job_id: string | null;
  name: string; agent_id: string | null; context: string | null;
  status: string | null; never_started: boolean; failed: boolean;
  brief: string; brief_truncated: boolean;
  result: string | null; result_truncated: boolean; error: string | null;
  created_at: number | null; closed_as: string | null;
  /** head_id stays null until the sub-agent has ended. */
  conversation: { session_id: string; head_id: string | null } | null;
  children: ActivitySubAgentEvent[];
  /** The time chart only: what it did in its own branch. */
  steps?: ActivityRunEvent[];
}
export type ActivityRunEvent =
  | ActivityAskedEvent | ActivityStepEvent | ActivityRepliedEvent
  | ActivityAttemptEvent | ActivitySubAgentEvent;

export interface ActivityRunEvents {
  run_id: string;
  revision: string;
  events: ActivityRunEvent[];
}

/** The runs of a branch (newest first, paged) or of one scheduled task. */
export function getActivityRuns(sessionId: string,
  query: { head?: string | null; offset?: number; limit?: number; taskId?: string; chart?: boolean },
  signal?: AbortSignal): Promise<ActivityRunPage> {
  const params = new URLSearchParams();
  if (query.taskId) params.set("task_id", query.taskId);
  else {
    if (query.head) params.set("head", query.head);
    if (query.offset !== undefined) params.set("offset", String(query.offset));
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.chart) params.set("chart", "true");
  }
  const suffix = params.toString();
  return jsonFetch<ActivityRunPage>(`/api/session/${encodeURIComponent(sessionId)}/activity/runs${suffix ? `?${suffix}` : ""}`, { signal, cache: "no-store" });
}

/** What happened inside one run, in time order. */
export function getActivityRunEvents(sessionId: string, runId: string,
  signal?: AbortSignal): Promise<ActivityRunEvents> {
  return jsonFetch<ActivityRunEvents>(`/api/session/${encodeURIComponent(sessionId)}/activity/runs/${encodeURIComponent(runId)}/events`, { signal, cache: "no-store" });
}
