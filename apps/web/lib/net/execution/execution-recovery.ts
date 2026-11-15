/** Read-only execution recovery, owned by one WebSocket connection.
 * The visible transcript starts first. Recovery yields between responses so
 * old cursors cannot fill the transport ahead of interactive requests. */
export function createExecutionRecovery(options: {
  send: (request: { action: "execution.replay"; execution_id: string; after_sequence: number; snapshot_only: true }) => void;
  schedule: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  cancel: (timer: ReturnType<typeof setTimeout>) => void;
}) {
  const pending = new Map<string, number>();
  let active: { id: string; after: number } | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let started = false;
  let disposed = false;

  function schedule() {
    if (!started || disposed || active || timer !== undefined || !pending.size) return;
    timer = options.schedule(() => {
      timer = undefined;
      if (disposed || active) return;
      const next = pending.entries().next().value;
      if (!next) return;
      const [id, after] = next;
      pending.delete(id);
      active = { id, after };
      timeout = options.schedule(() => complete(id), 15_000);
      try {
        options.send({ action: "execution.replay", execution_id: id, after_sequence: after, snapshot_only: true });
      } catch {
        complete(id);
      }
    }, 0);
  }
  function complete(id: string, terminal = false) {
    if (terminal) pending.delete(id);
    if (active?.id !== id) return;
    if (timeout !== undefined) options.cancel(timeout);
    timeout = undefined;
    active = undefined;
    schedule();
  }
  return {
    start() { if (!disposed) { started = true; schedule(); } },
    request(id: string, after: number) {
      if (disposed || !id || !Number.isSafeInteger(after) || after < 0) return;
      if (active?.id === id && active.after === after) return;
      pending.set(id, Math.min(pending.get(id) ?? after, after));
      schedule();
    },
    complete,
    dispose() {
      disposed = true;
      pending.clear();
      active = undefined;
      if (timer !== undefined) options.cancel(timer);
      if (timeout !== undefined) options.cancel(timeout);
      timer = timeout = undefined;
    },
  };
}

export function shouldReleaseAfterExecution(
  recovered: boolean,
  observedRunningExecution: boolean,
): boolean {
  // Reading an old completed snapshot is not a new completion. A task that
  // was live in this client can have finished while its connection was down.
  return !recovered || observedRunningExecution;
}
