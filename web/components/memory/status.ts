import type { CommitmentStatus, WriterStatus } from "./types";

export type WriterStatusState =
  | "unrecorded"
  | "up_to_date"
  | "pending"
  | "failed"
  | "pending_count_unavailable";

// Ordering comes from the server-stamped `last_outcome`, never from comparing
// the two timestamps here: two writes inside one millisecond are ordered on
// the writer's side and indistinguishable on this one.
export function writerStatusState(status: WriterStatus): WriterStatusState {
  if (status.last_failure && status.last_outcome === "failure") return "failed";
  if (status.pending_turns === null) return "pending_count_unavailable";
  if (status.pending_turns > 0) return "pending";
  if (!status.last_success_at && !status.last_failure) return "unrecorded";
  return "up_to_date";
}

export type CommitmentStatusState = "empty" | "open" | "closed";

export function commitmentStatusState(
  status: CommitmentStatus,
): CommitmentStatusState {
  if (status.counts.open > 0) return "open";
  return status.counts.total > 0 ? "closed" : "empty";
}
