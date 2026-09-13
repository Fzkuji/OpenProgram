import { idempotencyKeyFor, MutationRegistryCapacityError, wsMutationRequest } from "@/lib/net/ws-request";
/** Structured result helpers for FileTree mutations. */
import { invalidateFileRead, type ServerRenameResult } from "@/lib/files/files-shared";

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

export type FileOperation = (
  op: "create" | "rename" | "copy" | "delete" | "write" | "reveal",
  payload: Record<string, unknown>, refreshDirs: string[],
) => Promise<FileOperationResult>;
export type FileQuery = <T>(
  action: string, payload: Record<string, unknown>, responseType: string,
  canRun: () => boolean, outerSignal?: AbortSignal,
  controllerSet?: Set<AbortController>,
) => Promise<T | null>;
interface FileOperationContext {
  projectId: string;
  text: (en: string, zh: string) => string;
  fileQuery: FileQuery;
  mutationLifecycleGeneration: { current: number };
  mutationKeys: { current: Set<string> };
  mutationControllers: { current: Set<AbortController> };
  mutationRequestControllers: { current: Set<AbortController> };
}

export async function runFileOperation(
  context: FileOperationContext,
    op: "create" | "rename" | "copy" | "delete" | "write" | "reveal",
    payload: Record<string, unknown>,
    refreshDirs: string[],
  ): Promise<FileOperationResult> {
    const { projectId, text, fileQuery, mutationLifecycleGeneration, mutationKeys, mutationControllers, mutationRequestControllers } = context;
    const lifecycleGeneration = mutationLifecycleGeneration.current;
    const operationPayload = { project_id: projectId, ...payload };
    if (op === "reveal") {
      const data = await fileQuery<FileOperationResult>(
        "project_file_reveal", operationPayload, "project_file_reveal_result",
        () => true,
      );
      return data
        ? { ...data, status: data.status ?? (data.ok ? "ready" : "error") }
        : { status: "error", error_code: "TRANSPORT_ERROR" };
    }
    let operationKey: string;
    try {
      operationKey = idempotencyKeyFor(`project_file_${op}`, operationPayload);
    } catch (error) {
      if (error instanceof MutationRegistryCapacityError) {
        window.alert(text("Too many file operations are still pending.", "仍有太多文件操作未完成。"));
      }
      return { status: "error", error_code: "MUTATION_REGISTRY_CAPACITY" };
    }
    const operationController = new AbortController();
    mutationKeys.current.add(operationKey);
    mutationControllers.current.add(operationController);
    let data: FileOperationResult | null = null;
    try {
      data = await wsMutationRequest<FileOperationResult>(
        operationKey,
        (signal) => fileQuery<FileOperationResult>(
          `project_file_${op}`,
          { ...operationPayload, idempotency_key: operationKey },
          `project_file_${op}_result`,
          // Durable mutations must keep their own request lifecycle. A
          // project-files-changed event invalidates query generations, but it
          // cannot cancel a mutation receipt that may still be in progress.
          () => true,
          signal,
          mutationRequestControllers.current,
        ),
        { signal: operationController.signal },
      );
    } catch {
      data = null;
    } finally {
      mutationControllers.current.delete(operationController);
      mutationKeys.current.delete(operationKey);
    }
    const result: FileOperationResult = data
      ? { ...data, status: data.status ?? (data.ok ? "ready" : "error") }
      : { status: "error", error_code: "TRANSPORT_ERROR" };
    // The server may still have accepted the durable operation after this
    // component was replaced. Keep its idempotency key for replay, but do not
    // let the old component alert or broadcast into the new project view.
    if (operationController.signal.aborted
      || lifecycleGeneration !== mutationLifecycleGeneration.current) return result;
    if (!data || data.project_id !== projectId || data.path !== payload.path
      || result.error || result.status === "conflict" || result.status === "recovery_required"
      || result.status === "error" || result.status === "in_progress") {
      if (data?.error || data?.error_code) window.alert(data.error ?? data.error_code);
      return result;
    }
    // The project-files-changed listener performs one generation-safe refresh;
    // avoid starting directory requests that the event would immediately abort.
    void refreshDirs;
    if (op === "rename") {
      invalidateFileRead(projectId, String(payload.path ?? ""));
      invalidateFileRead(projectId, String(payload.new_path ?? ""));
    } else if (op === "delete" || op === "write") {
      invalidateFileRead(projectId, String(payload.path ?? ""));
    }
    window.dispatchEvent(new CustomEvent("project-files-changed", {
      detail: { project_id: projectId },
    }));
    return result;
  }


