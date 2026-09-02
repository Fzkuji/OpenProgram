/** Structured result helpers for FileTree mutations. */
import type { ServerRenameResult } from "@/lib/state/files-shared";

export interface FileOperationResult {
  project_id?: string;
  path?: string;
  status: "ready" | "error" | "conflict" | "recovery_required" | "in_progress";
  ok?: boolean;
  error_code?: string;
  error?: string;
  idempotency_key?: string;
  operation_id?: string;
}

export function asServerRenameResult(
  result: FileOperationResult,
  failureStatus: "error" | "recovery_required" = "error",
): ServerRenameResult {
  return {
    status: result.status === "ready"
      ? "ready"
      : result.status === "recovery_required"
        ? "recovery_required"
        : failureStatus,
    error_code: result.error_code,
    error: result.error,
    idempotency_key: result.idempotency_key,
    operation_id: result.operation_id,
  };
}

