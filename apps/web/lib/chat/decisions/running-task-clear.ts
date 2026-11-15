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

/** Two identities name the same turn: by execution when both know theirs,
 *  else by message. A slot learned before admission (a load during the
 *  reservation) names only its message, and a clear may too. */
export function shouldHonorRunningTaskClear(
  current: RunningTaskIdentity | undefined,
  cleared?: ClearedTaskIdentity,
): boolean {
  if (!current) return true;
  const execution = (current.execution_id || "").trim();
  const clearedExecution = (cleared?.execution_id || "").trim();
  if (execution && clearedExecution) return execution === clearedExecution;
  const mid = (current.msg_id || "").trim();
  return Boolean(mid && mid === (cleared?.msg_id || "").trim());
}
