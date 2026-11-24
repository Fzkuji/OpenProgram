/** The window's side of the Worker's change notices, without React: who
 *  listens to which topic, and the socket coming back. `changes.ts` adds the
 *  hook that reads again when told. */
export type Handler = (keys: readonly string[] | null) => void;

const handlers = new Map<string, Set<Handler>>();

/** Call `handler` whenever `topic` changes. Returns the unsubscribe. */
export function onChanged(topic: string, handler: Handler): () => void {
  let set = handlers.get(topic);
  if (!set) handlers.set(topic, (set = new Set()));
  set.add(handler);
  return () => {
    set.delete(handler);
    if (set.size === 0) handlers.delete(topic);
  };
}

/** A `changed` frame from the Worker. */
export function dispatchChanged(topic: string, keys: readonly string[] | null): void {
  for (const handler of [...(handlers.get(topic) ?? [])]) {
    try {
      handler(keys);
    } catch (error) {
      console.error("[changes] handler failed", topic, error);
    }
  }
}

const reconnectHandlers = new Set<() => void>();

/** Call `handler` each time the socket comes back, for a cache that holds
 *  answers of every topic. Returns the unsubscribe. */
export function onReconnected(handler: () => void): () => void {
  reconnectHandlers.add(handler);
  return () => { reconnectHandlers.delete(handler); };
}

/** The socket came back: whatever changed meanwhile was not told. */
export function dispatchReconnected(): void {
  for (const topic of [...handlers.keys()]) dispatchChanged(topic, null);
  for (const handler of [...reconnectHandlers]) {
    try {
      handler();
    } catch (error) {
      console.error("[changes] reconnect handler failed", error);
    }
  }
}
