/** A scheduled task's runs, a page at a time, for its Activity page. */
import type { TaskRunOutcome, TaskRunSummary } from "@/lib/chat/conversation-work";
import { jsonFetch } from "../fetch-client";

export type TaskRunFilter = "all" | "unfinished" | "notified";

export interface TaskRun {
  execution_id: string;
  started_at: number;
  /** null while it runs. */
  ended_at: number | null;
  status: string;
  reason_code: string | null;
  outcome: TaskRunOutcome;
  /** Why it did not finish or could not check, one sentence in each language. */
  reason: { en: string; zh: string } | null;
  /** Dollars, its helpers included; null when no price is known. */
  cost: number | null;
}

export interface TaskRunPage extends TaskRunSummary {
  task_id: string;
  /** Every run's cost added up; null when none is known. */
  cost: number | null;
  filter: TaskRunFilter;
  offset: number;
  /** Runs the filter keeps. */
  total: number;
  page: TaskRun[];
}

export function getTaskRuns(sessionId: string, taskId: string,
  query: { filter: TaskRunFilter; offset: number; limit: number }, signal?: AbortSignal): Promise<TaskRunPage> {
  const params = new URLSearchParams({ filter: query.filter, offset: String(query.offset), limit: String(query.limit) });
  return jsonFetch<TaskRunPage>(`/api/session/${encodeURIComponent(sessionId)}/activity/tasks/${encodeURIComponent(taskId)}?${params}`, { signal, cache: "no-store" });
}
