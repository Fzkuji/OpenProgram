/** When each message reached this window live (appended, not loaded with
 *  its history). A row plays its appear animation only right after its
 *  message arrives; a transcript that mounts or comes back into view with
 *  the same rows shows them as they are. */
const arrivals = new Map<string, number>();
const KEEP_MS = 5000;

export function noteMessageArrival(id: string): void {
  const now = performance.now();
  if (arrivals.size > 200) for (const [key, at] of arrivals) if (now - at > KEEP_MS) arrivals.delete(key);
  arrivals.set(id, now);
}

export function rekeyMessageArrival(oldId: string, newId: string): void {
  const at = arrivals.get(oldId);
  if (at === undefined) return;
  arrivals.delete(oldId);
  arrivals.set(newId, at);
}

export function messageArrivedWithin(id: string, ms: number): boolean {
  const at = arrivals.get(id);
  return at !== undefined && performance.now() - at < ms;
}
