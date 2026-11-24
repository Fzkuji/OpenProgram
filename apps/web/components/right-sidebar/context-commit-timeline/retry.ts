/** How the Context tab asks again for what has not come back. */

/** The wait before the next ask: 1 s, doubling, never more than 30 s. */
export function retryDelay(failures: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, failures));
}

/** A commit's detail reply either carries its items, or is the server's
 *  answer that it has none to give (not found, a missing id, a read it
 *  refused). That answer is final: the row shows no items and is not asked
 *  again. */
export function detailReplyOutcome(reply: { error?: unknown; items?: unknown }): "kept" | "refused" {
  return !reply.error && Array.isArray(reply.items) ? "kept" : "refused";
}
