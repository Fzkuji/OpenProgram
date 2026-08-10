import type { WriterStatus } from "./types";

export type WriterStatusState =
  | "empty"
  | "idle"
  | "pending"
  | "failed"
  | "unavailable";

export function writerStatusState(status: WriterStatus): WriterStatusState {
  const failureAt = status.last_failure
    ? Date.parse(status.last_failure.at)
    : Number.NaN;
  const successAt = status.last_success_at
    ? Date.parse(status.last_success_at)
    : Number.NaN;
  if (status.last_failure && (
    !Number.isFinite(successAt)
    || !Number.isFinite(failureAt)
    || failureAt > successAt
  )) return "failed";
  if (status.pending_turns === null) return "unavailable";
  if (status.pending_turns > 0) return "pending";
  if (!status.last_success_at && !status.last_failure) return "empty";
  return "idle";
}
