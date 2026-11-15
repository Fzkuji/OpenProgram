/** What a transcript row's status means, decided once.
 *
 *  A row is live while something still writes it, held while its run waits
 *  on the user, settled when nothing more will change. A spawned agent's
 *  status comes from its job and has its own words, in its own table. */

const ROW_PHASE = new Map<string, "live" | "held" | "settled">([
  ["pending", "live"],
  ["running", "live"],
  ["streaming", "live"],
  ["cancelling", "live"],
  ["paused", "held"],
  ["done", "settled"],
  ["completed", "settled"],
  ["error", "settled"],
  ["cancelled", "settled"],
  ["interrupted", "settled"],
]);

/** Every status a row can carry. */
export const ROW_STATUSES: readonly string[] = [...ROW_PHASE.keys()];

const rowPhase = (status: string | null | undefined) => ROW_PHASE.get(status ?? "");

/** Something is still writing this row: it draws as working. */
export function isLive(status: string | null | undefined): boolean {
  return rowPhase(status) === "live";
}

/** The row's work has not ended, though it may be waiting on the user. */
export function isOpen(status: string | null | undefined): boolean {
  const phase = rowPhase(status);
  return phase === "live" || phase === "held";
}

/** Nothing more will change this row. A row with no status is neither open
 *  nor settled: old records carry none. */
export function isSettled(status: string | null | undefined): boolean {
  return rowPhase(status) === "settled";
}

/** The row ended because it was stopped or cut off, not because it failed. */
export function isStopped(status: string | null | undefined): status is "cancelled" | "interrupted" {
  return status === "cancelled" || status === "interrupted";
}

/** The reader stopped this row, whether or not the stop has finished. */
export function isBeingStopped(status: string | null | undefined): boolean {
  return status === "cancelling" || status === "cancelled";
}

/** A stored row's status as a reload shows it. A row left cancelling by a
 *  reload has finished cancelling; older rows with no status take theirs from
 *  the row type. */
export function storedRowStatus(
  status: string | null | undefined,
  rowType?: string | null,
): "running" | "streaming" | "paused" | "done" | "error" | "cancelled" | "interrupted" {
  switch (status) {
    case "running":
    case "streaming":
    case "paused":
    case "error":
    case "cancelled":
    case "interrupted":
      return status;
    case "cancelling":
      return "cancelled";
    default:
      return rowType === "error" ? "error" : "done";
  }
}

/** The status a row takes from its execution's status. */
export function rowStatusForExecution(status: unknown): string {
  const value = String(status || "").toLowerCase();
  switch (value) {
    case "completed":
    case "done":
      return "done";
    case "failed":
    case "error":
    case "reconciliation_required":
      return "error";
    case "cancelled":
    case "interrupted":
    case "cancelling":
    case "paused":
      return value;
    default:
      return "running";
  }
}

const AGENT_PHASE = new Map<string, "running" | "cancelling" | "finished">([
  ["pending", "running"],
  ["queued", "running"],
  ["running", "running"],
  ["cancelling", "cancelling"],
  ["completed", "finished"],
  ["errored", "finished"],
  ["failed", "finished"],
  ["cancelled", "finished"],
]);

/** Every status a spawned agent's card can carry. */
export const AGENT_STATUSES: readonly string[] = [...AGENT_PHASE.keys()];

/** The agent is queued or working. */
export function isAgentRunning(status: string | null | undefined): boolean {
  return AGENT_PHASE.get(status ?? "") === "running";
}

/** The agent has not finished: working, or still being cancelled. */
export function isAgentLive(status: string | null | undefined): boolean {
  const phase = AGENT_PHASE.get(status ?? "");
  return phase === "running" || phase === "cancelling";
}

/** The agent's job has ended. */
export function isAgentFinished(status: string | null | undefined): boolean {
  return AGENT_PHASE.get(status ?? "") === "finished";
}
