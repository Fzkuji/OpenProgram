/**
 * What a branch's running operation is, read from its running task.
 *
 * The server names every foreground operation by kind (`reply`, `compact`,
 * `rewind`, `retry`, `edit`, `function`) and gives one with no execution an
 * `op_id` that Stop names. A turn that compacts its own context mid-run
 * carries `phase: "compact"` while it does.
 */
import type { RunningTask } from "@/lib/session-store/types";

type Text = (english: string, chinese: string) => string;

/** The context is being compacted: /compact, or a turn's own compaction. */
export function isCompacting(task: RunningTask | null | undefined): boolean {
  return task?.op_kind === "compact" || task?.phase === "compact";
}

/** An operation that runs without an execution; Stop cancels it by `op_id`. */
export function stopsByOperation(task: RunningTask | null | undefined): boolean {
  return Boolean(task?.op_id) && !task?.execution_id;
}

/** The run indicator's words for an operation that is not a reply, or null. */
export function operationStatusText(task: RunningTask | null | undefined, text: Text): string | null {
  if (isCompacting(task)) return text("Compacting context…", "正在压缩上下文…");
  if (task?.op_kind === "rewind") return text("Rewinding…", "正在回退…");
  return null;
}

/** The `operation.cancel` command for a Stop of `task`, or null when the
 *  task is a turn (it is stopped through its execution). */
export function operationCancelCommand(sessionId: string, task: RunningTask | null | undefined) {
  if (!task || !stopsByOperation(task)) return null;
  return { action: "operation.cancel", session_id: sessionId, op_id: task.op_id as string };
}
