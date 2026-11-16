/**
 * The React key each transcript row is drawn under.
 *
 * A message the person sends is drawn at once under a local id, and takes
 * the server's id when the server acknowledges it (or when the server's own
 * row arrives first). Both rows carry the id the page gave the request, so
 * the row is keyed by that: the acknowledgement changes fields of a row
 * that stays mounted, and the bubble does not start over.
 */
export function rowRenderKeys(
  ids: readonly string[],
  rows: Readonly<Record<string, { clientRequestId?: string } | undefined>>,
): (id: string) => string {
  let keys: Map<string, string> | null = null;
  const taken = new Set<string>();
  for (const id of ids) {
    const request = rows[id]?.clientRequestId;
    if (!request || taken.has(request)) continue;
    taken.add(request);
    (keys ??= new Map()).set(id, `send:${request}`);
  }
  return (id) => keys?.get(id) ?? id;
}
