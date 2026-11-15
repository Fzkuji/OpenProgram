export type TurnHistoryOperation = "undo" | "revert" | "redo" | "reapply";

/** Why the history action is withheld, as reported by ``turn_history_state``. */
export type TurnHistoryReason =
  | "files_changed"
  | "not_recoverable"
  | "inactive_branch"
  | "agent_running"
  | "run_active"
  | "in_progress"
  | "spans_turns";

export interface TurnHistoryState {
  status: string;
  operation: TurnHistoryOperation | null;
  reason?: string;
  error?: string;
}

/** What the card shows for a list holding more than one turn's files. The
 *  server's answer is about the last turn only, so it is not asked. */
export const SPANS_TURNS_STATE: TurnHistoryState = { status: "blocked", operation: null, reason: "spans_turns" };

/** Notices keyed by reason; ``fallback`` covers states with no known reason. */
export type TurnHistoryNotices = Partial<Record<TurnHistoryReason, string>> & { fallback: string };

/**
 * Why the card's history action is withheld, shown in the hover tip of the
 * greyed action. A failed action reports its own error; otherwise the reason
 * picks the copy. Engine error text is never shown as the explanation.
 */
export function historyPresentation(
  state: TurnHistoryState | null,
  transientError: string,
  notices: TurnHistoryNotices,
) {
  if (transientError) return { notice: transientError, operation: null };
  if (state && state.status !== "ready") {
    const known = state.reason ? notices[state.reason as TurnHistoryReason] : undefined;
    return { notice: known || notices.fallback, operation: null };
  }
  return { notice: "", operation: state?.operation ?? null };
}
