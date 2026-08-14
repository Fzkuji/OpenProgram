import type { CommitmentStatus } from "./types";

export type CommitmentStatusState = "empty" | "open" | "closed";

export function commitmentStatusState(
  status: CommitmentStatus,
): CommitmentStatusState {
  if (status.counts.open > 0) return "open";
  return status.counts.total > 0 ? "closed" : "empty";
}
