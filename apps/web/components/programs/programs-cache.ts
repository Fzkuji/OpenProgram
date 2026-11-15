/** In-memory conditional reads. Pending checks are shared across remounts. */
export function createProgramsCache(fetchJson: (url: string, init?: RequestInit) => Promise<unknown>) {
  const entries = new Map<string, { value: unknown; revision?: string }>();
  const pending = new Map<string, Promise<unknown>>();
  return {
    peek<T>(kind: string, path = ""): T | undefined {
      return entries.get(`${kind}:${path}`)?.value as T | undefined;
    },
    async read<T>(kind: string, path = "", signal?: AbortSignal): Promise<T> {
      const key = `${kind}:${path}`;
      let work = pending.get(key);
      if (!work) {
        const previous = entries.get(key);
        const params = new URLSearchParams({ path });
        if (previous?.revision) params.set("revision", previous.revision);
        work = fetchJson(`/api/programs/${kind}?${params}`, { signal: AbortSignal.timeout(15000) }).then(raw => {
          const result = raw as { unchanged?: boolean; revision?: string };
          if (result.unchanged) {
            if (!previous) throw new Error("Programs cache has no matching revision");
            return previous.value;
          }
          entries.delete(key);
          entries.set(key, { value: raw, revision: result.revision });
          while (entries.size > 128) entries.delete(entries.keys().next().value!);
          return raw;
        }).finally(() => { pending.delete(key); });
        pending.set(key, work);
      }
      const value = await work;
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return value as T;
    },
  };
}
