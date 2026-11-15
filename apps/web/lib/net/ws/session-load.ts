export type SessionReadStatus = 'loading' | 'ready' | 'error' | 'disconnected';
export type SessionRead = { action: 'load_session'; session_id: string; [key: string]: unknown };

/** One connection owns pending reads. Mutations invalidate reads, never retry commands. */
export function createSessionLoads(options: {
  send: (request: SessionRead) => void;
  status: (id: string, status: SessionReadStatus) => void;
  requestId: () => string;
}) {
  type Pending = { request: SessionRead; timer: ReturnType<typeof setTimeout>; next?: SessionRead };
  const pending = new Map<string, Pending>();
  const seen = new Set<string>();
  let disposed = false;
  type Exclusive = { start: () => void; skip: () => void };
  const running = new Map<string, { next?: Exclusive }>();
  function runExclusive(view: string, turn: Exclusive) {
    running.set(view, {});
    turn.start();
  }
  function finishExclusive(view: string) {
    const next = running.get(view)?.next;
    running.delete(view);
    if (next) runExclusive(view, next);
  }
  function request(payload: SessionRead, invalidate = false): boolean {
    if (disposed) return false;
    const id = payload.session_id;
    const active = pending.get(id);
    if (active) {
      if (invalidate) active.next = payload;
      return true;
    }
    seen.add(id);
    const wire = { ...payload, request_id: options.requestId() };
    const entry: Pending = {
      request: wire,
      timer: setTimeout(() => fail(id, 'error'), 15_000),
    };
    pending.set(id, entry);
    options.status(id, 'loading');
    try { options.send(wire); } catch { fail(id, 'disconnected'); return false; }
    return true;
  }
  function take(id: string) {
    const entry = pending.get(id);
    if (entry) { clearTimeout(entry.timer); pending.delete(id); }
    return entry;
  }
  function fail(id: string, status: SessionReadStatus) {
    const entry = take(id);
    if (!entry) return;
    if (entry.next && status === 'error' && !disposed) { request(entry.next); return; }
    options.status(id, status);
  }
  return {
    request,
    /** A repair or page read: one in flight and one pending per view. A newer
     *  read replaces the pending one, which resolves as ``skipped``. */
    exclusive<R>(view: string, read: () => Promise<R>, skipped: R): Promise<R> {
      return new Promise<R>((resolve, reject) => {
        const turn: Exclusive = {
          start: () => {
            read().then(resolve, reject).finally(() => finishExclusive(view));
          },
          skip: () => resolve(skipped),
        };
        const active = running.get(view);
        if (!active) { runExclusive(view, turn); return; }
        active.next?.skip();
        active.next = turn;
      });
    },
    /** Whether a repair or page read of the view is in flight; a repair that
     *  finds one folds into it, since that read answers from the current state. */
    busy(view: string): boolean {
      return running.has(view);
    },
    /** ``next`` is a read that arrived meanwhile. The caller applies this reply,
     *  then sends it, so a stream of invalidations cannot starve the view. */
    accept(data: { id?: unknown; request_id?: unknown }): { next?: SessionRead } | false {
      if (disposed || typeof data.id !== 'string') return false;
      const entry = pending.get(data.id);
      // Unsolicited legacy loads are allowed only before a managed read.
      if (!entry) return !data.request_id && !seen.has(data.id) ? {} : false;
      if (data.request_id !== entry.request.request_id) return false;
      take(data.id);
      options.status(data.id, 'ready');
      return { next: entry.next };
    },
    error(data: { request_id?: unknown }) {
      for (const [id, entry] of pending) {
        if (data.request_id === entry.request.request_id) { fail(id, 'error'); return true; }
      }
      return false;
    },
    dispose() {
      disposed = true;
      for (const id of pending.keys()) fail(id, 'disconnected');
      seen.clear();
    },
  };
}
