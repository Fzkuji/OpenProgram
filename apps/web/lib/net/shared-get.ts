const running = new Map<string, Promise<unknown>>();

/**
 * A read that callers asking at the same moment share: several parts of a
 * page want the same list as it opens, and each used to send its own
 * request. `read` is shared only while it is in flight; a later call reads
 * again. `key` names what is read, usually its address.
 */
export function sharedRead<T>(key: string, read: () => Promise<T>): Promise<T> {
  const current = running.get(key);
  if (current) return current as Promise<T>;
  const request = read().finally(() => {
    if (running.get(key) === request) running.delete(key);
  });
  running.set(key, request);
  return request;
}
