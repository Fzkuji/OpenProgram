/** Order the frames that report whether a session is running.

`running_task`, `running_task_clear`, `run_state` and `session_loaded` each
carry the server's `run_seq`, taken before the server read the run state.
They can arrive out of order: a session load reads "running", builds the
transcript, and is sent after the turn's clear was already broadcast. A
frame is applied only if its run_seq is newer than the last one applied for
that session, so a stale "running" can never bring back a finished turn and
a stale clear can never idle a newer one.

Frames without a run_seq (local placeholders, older servers) are applied as
before; the clear's own identity check still guards them.
*/

const lastRunSeq = new Map<string, number>();

export function acceptRunFrame(sessionId: string | undefined, runSeq: unknown): boolean {
  if (!sessionId || typeof runSeq !== "number") return true;
  if (runSeq <= (lastRunSeq.get(sessionId) ?? 0)) return false;
  lastRunSeq.set(sessionId, runSeq);
  return true;
}

export function resetRunSeqForTests(): void {
  lastRunSeq.clear();
}
