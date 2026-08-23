export type TurnHistoryOperation = "undo" | "revert" | "redo" | "reapply";

export interface TurnHistoryState {
  status: string;
  operation: TurnHistoryOperation | null;
  error?: string;
}

export function historyPresentation(
  state: TurnHistoryState | null,
  transientError: string,
  fallbackNotice: string,
) {
  const notice = transientError || (
    state && state.status !== "ready" ? state.error || fallbackNotice : ""
  );
  return {
    notice,
    operation: notice ? null : (state?.operation ?? null),
  };
}
