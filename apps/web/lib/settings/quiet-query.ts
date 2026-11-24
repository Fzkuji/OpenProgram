/**
 * Settings reads made through TanStack Query: kept for the whole visit (a
 * revisit shows the last answer at once and refreshes it behind), retried
 * quietly with backoff while the page shows it, and a failure is logged,
 * never put on screen.
 */
export const QUIET_QUERY = {
  gcTime: Infinity,
  retry: true,
  retryDelay: (attempt: number) => Math.min(1000 * 2 ** attempt, 30_000),
} as const;

/** `read`, logging each failure before TanStack Query tries again. */
export function warnOnFailure<T>(what: string, read: () => Promise<T>): () => Promise<T> {
  return () => read().catch((error: unknown) => {
    console.warn(`Settings read ${what} failed; trying again quietly`, error);
    throw error;
  });
}
