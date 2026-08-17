/**
 * Handle on the app's react-query client for non-React callers.
 *
 * `app/providers.tsx` owns the instance and registers it here; plain
 * modules (`lib/net/use-ws.ts` invalidating caches on server pushes)
 * read it back instead of reaching through `window`.
 */
import type { QueryClient } from "@tanstack/react-query";

let client: QueryClient | null = null;

export function setQueryClient(c: QueryClient): void {
  client = c;
}

export function getQueryClient(): QueryClient | null {
  return client;
}
