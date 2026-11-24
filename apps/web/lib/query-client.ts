/**
 * The app's react-query client: how long answers are kept and how failed
 * reads behave, plus a handle on it for non-React callers.
 *
 * `app/providers.tsx` creates the instance with `createAppQueryClient`,
 * registers it here and has it follow the Worker's change frames
 * (`followQueryTopics`); plain modules (`lib/net/ws/use-ws.ts` invalidating
 * caches on server pushes) read it back instead of reaching through
 * `window`.
 *
 * An answer is fresh until something says it changed: a change this window
 * made invalidates its keys, a Worker topic below invalidates the keys read
 * from it, and a reconnect invalidates everything. Its age does not matter.
 * A viewer of an invalidated answer sees it at once while it is read again.
 */
import { QueryCache, QueryClient, type QueryKey } from "@tanstack/react-query";
import { onChanged, onReconnected } from "@/lib/net/ws/change-bus";

/** Which cached answers each Worker topic makes old, by key prefix. */
export const QUERY_TOPICS: Readonly<Record<string, readonly QueryKey[]>> = {
  // config.json: enabled models, providers, Agents' defaults.
  settings: [["models-enabled"], ["providers"], ["models"], ["provider-fallback"], ["agents"]],
  // A credential changed or a provider call was recorded: quotas, keys and
  // what a refused call could fall back to.
  routing: [["models-enabled", "provider-status"], ["provider-credentials"], ["session-credentials"], ["provider-fallback"]],
  agents: [["agents"]],
  applications: [["applications"]],
};

/** Invalidate cached answers when their topic changes or the socket comes
 *  back. Returns the stop. */
export function followQueryTopics(client: QueryClient): () => void {
  const offs = Object.entries(QUERY_TOPICS).map(([topic, keys]) => onChanged(topic, () => {
    for (const queryKey of keys) void client.invalidateQueries({ queryKey });
  }));
  offs.push(onReconnected(() => { void client.invalidateQueries(); }));
  return () => { for (const off of offs) off(); };
}

/** Answers stay this long after their last viewer unmounts, so going back
 *  to a page shows its data at once instead of reading it from nothing (the
 *  library's 5 minutes dropped it between ordinary visits). */
export const QUERY_KEEP_MS = 30 * 60_000;

/** A request the worker answered with 4xx: asking again gets the same. */
function isClientError(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" && status >= 400 && status < 500;
}

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      // A failed background read keeps its last data and tries again on
      // its own; it is logged, never shown.
      onError: (error, query) => console.warn("[query] read failed", query.queryKey, error),
    }),
    defaultOptions: {
      queries: {
        staleTime: Infinity,
        gcTime: QUERY_KEEP_MS,
        refetchOnWindowFocus: false,
        // Quiet retries with the library's backoff (1 s doubling to 30 s)
        // for as long as the worker cannot answer; a 4xx answer is final.
        retry: (_failures, error) => !isClientError(error),
      },
    },
  });
}

let client: QueryClient | null = null;

export function setQueryClient(c: QueryClient): void {
  client = c;
}

export function getQueryClient(): QueryClient | null {
  return client;
}
