/** Decide whether a running_task_clear should idle this session.

A queued "stop current and send now" releases the old turn, then starts
the next immediately. The old turn still emits running_task_clear /
cancelled / result. Treating that as "session idle" makes the new turn
look finished the moment it starts.

Honor the clear only when it names the same execution the slot holds.
A just-sent placeholder (no msg_id yet) and a newer execution both
survive a stale or unscoped clear.
*/

export type RunningTaskIdentity = {
  msg_id?: string;
  execution_id?: string;
  started_at?: number;
};

export type ClearedTaskIdentity = {
  msg_id?: string;
  execution_id?: string;
};

export function executionKey(
  task?: RunningTaskIdentity | ClearedTaskIdentity | null,
): string {
  if (!task) return "";
  const exec = (task.execution_id || "").trim();
  if (exec) return exec;
  const mid = (task.msg_id || "").trim();
  return mid ? `${mid}_reply` : "";
}

export function shouldHonorRunningTaskClear(
  current: RunningTaskIdentity | undefined,
  cleared?: ClearedTaskIdentity,
): boolean {
  if (!current) return true;
  const cur = executionKey(current);
  const gone = executionKey(cleared);
  if (cur && gone) return cur === gone;
  return false;
}
