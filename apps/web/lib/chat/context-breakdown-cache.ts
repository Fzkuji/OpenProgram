export interface ContextBreakdown {
  messages?: number;
  system_prompt?: number;
  skills?: number;
  memory?: number;
  tools_schema?: number;
  tools_deferred_catalog?: number;
  mcp_tools?: number;
  mcp_tools_deferred?: number;
  unclassified?: number;
  classified_estimate?: number;
  classification_scale?: number;
  input_used?: number;
  free_space?: number | null;
  total_used?: number;
  /** ``null`` when the model's window is not known. */
  window?: number | null;
  basis?: string;
  tools?: Array<{ name: string; tokens: number; deferred: boolean; description?: string }>;
  skills_detail?: Array<{ name: string; source: string; tokens: number }>;
  /** Each layer's MEMORY.md, with the text injected. */
  memory_detail?: Array<{ path: string; tokens: number; text?: string }>;
  /** The System prompt row by component (base, environment, AGENTS.md…). */
  system_detail?: Array<{ name: string; tokens: number; text?: string }>;
  mcp_detail?: Array<{
    server: string; name: string; tokens: number; deferred: boolean; description?: string;
  }>;
  /** Messages by kind, and the largest messages on the branch. */
  messages_detail?: {
    groups: Array<{ kind: string; tokens: number; count: number }>;
    top: Array<{ id: string; kind: string; tokens: number; preview: string }>;
  };
  /** The compaction in effect on the branch: how many messages its summary
   *  covers, the occupancy before and after, and when it ran (epoch seconds). */
  last_compaction?: {
    covered: number;
    tokens_before?: number;
    tokens_after?: number;
    at?: number;
  };
  context_window?: number | null;
  model?: string;
  error?: string;
}

const CACHE_LIMIT = 32;
const cache = new Map<string, ContextBreakdown>();
const latestRequest = new Map<string, symbol>();
const listeners = new Set<() => void>();

function cacheKey(sessionId: string | null, headId?: string | null): string | null {
  return sessionId ? JSON.stringify([sessionId, headId ?? null]) : null;
}

function notify(): void {
  for (const listener of listeners) listener();
}

function touch(key: string, value: ContextBreakdown): void {
  cache.delete(key);
  cache.set(key, value);
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
  notify();
}

export function subscribeContextBreakdownCache(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function readContextBreakdownCache(
  sessionId: string | null,
  headId?: string | null,
): ContextBreakdown | null {
  const exact = cacheKey(sessionId, headId);
  if (!exact) return null;
  const hit = cache.get(exact);
  if (hit) {
    cache.delete(exact);
    cache.set(exact, hit);
    return hit;
  }
  if (!sessionId) return null;
  const latest = cache.get(JSON.stringify([sessionId, null]));
  return latest ?? null;
}

/** Whether this branch has its own snapshot. ``readContextBreakdownCache``
 *  also answers with the session's latest one, which may be another
 *  branch's: counted against that branch's model and window. */
export function hasContextBreakdown(sessionId: string, headId?: string | null): boolean {
  return cache.has(JSON.stringify([sessionId, headId ?? null]));
}

export function writeContextBreakdownCache(
  sessionId: string,
  headId: string | null | undefined,
  value: ContextBreakdown,
): void {
  touch(JSON.stringify([sessionId, headId ?? null]), value);
  if (headId != null) {
    // Session-latest copy so a panel that opens before head_id arrives
    // still paints immediately.
    const latestKey = JSON.stringify([sessionId, null]);
    cache.set(latestKey, value);
  }
}

type ContextBreakdownFetcher = (
  url: string,
  init: { signal: AbortSignal },
) => Promise<{ json: () => Promise<ContextBreakdown> }>;

export async function refreshContextBreakdown(
  sessionId: string,
  headId: string | null | undefined,
  signal: AbortSignal,
  fetcher: ContextBreakdownFetcher = (url, init) => fetch(url, init),
): Promise<ContextBreakdown | null> {
  const key = JSON.stringify([sessionId, headId ?? null]);
  const cached = cache.get(key) ?? cache.get(JSON.stringify([sessionId, null])) ?? null;
  const requestToken = Symbol();
  latestRequest.set(key, requestToken);
  const isCurrent = () => latestRequest.get(key) === requestToken;
  const qs = headId ? `?head_id=${encodeURIComponent(headId)}` : "";
  try {
    const response = await fetcher(
      `/api/sessions/${encodeURIComponent(sessionId)}/context${qs}`,
      { signal },
    );
    if (signal.aborted || !isCurrent()) return null;
    const data = await response.json();
    if (signal.aborted || !isCurrent()) return null;
    if (data.error) return cached ?? data;
    writeContextBreakdownCache(sessionId, headId, data);
    return data;
  } catch (error) {
    if (signal.aborted || !isCurrent()) return null;
    return cached ?? { error: String(error) };
  } finally {
    if (isCurrent()) latestRequest.delete(key);
  }
}

// Opening a conversation reaches this from several places within a moment;
// one read serves them all.
const FOCUS_READ_WINDOW_MS = 1500;
const lastRead = new Map<string, number>();

/** Background read/update for the panel snapshot.
 *
 *  Call on session focus and again when a turn finishes. Skips when the
 *  same branch is already in flight, and for `focus` reads when it was just
 *  read. A cache hit alone is not a skip, because the graph may have moved.
 */
export function warmContextBreakdown(
  sessionId: string | null,
  headId?: string | null,
  { focus = false }: { focus?: boolean } = {},
): void {
  if (!sessionId) return;
  const key = JSON.stringify([sessionId, headId ?? null]);
  if (latestRequest.has(key)) return;
  const now = Date.now();
  if (focus && now - (lastRead.get(key) ?? -Infinity) < FOCUS_READ_WINDOW_MS) return;
  lastRead.set(key, now);
  void refreshContextBreakdown(sessionId, headId, new AbortController().signal);
}

/** A chat with no session yet: the agent's system prompt and tools against
 *  the selected model, with the selected project's memory, from
 *  GET /api/context/draft. Keyed by model and project, so switching either
 *  refetches. */
function draftKey(provider?: string, model?: string, projectId?: string | null): string {
  return JSON.stringify(["__draft__", provider || "", model || "", projectId || ""]);
}

export function readDraftContextBreakdown(
  provider?: string,
  model?: string,
  projectId?: string | null,
): ContextBreakdown | null {
  return cache.get(draftKey(provider, model, projectId)) ?? null;
}

/** A draft's first message was accepted and the draft became a session
 *  (the server adopts the draft's key as the session id). The conversation
 *  and its content are the same, so the draft's estimate stands for the
 *  session until the session's own GET /context, started here, and then
 *  its context_stats replace it: the ring never drops to 0 or loses its
 *  window in between. A session that already has a snapshot keeps it. */
export function adoptDraftContextBreakdown(
  sessionId: string,
  provider?: string,
  model?: string,
  projectId?: string | null,
): void {
  const draft = readDraftContextBreakdown(provider, model, projectId);
  if (draft && !readContextBreakdownCache(sessionId)) {
    writeContextBreakdownCache(sessionId, null, draft);
  }
  warmContextBreakdown(sessionId, null, { focus: true });
}

export function warmDraftContextBreakdown(
  provider?: string,
  model?: string,
  projectId?: string | null,
): void {
  const key = draftKey(provider, model, projectId);
  if (latestRequest.has(key)) return;
  const token = Symbol();
  latestRequest.set(key, token);
  const qs = new URLSearchParams({ provider: provider || "", model: model || "" });
  if (projectId) qs.set("project_id", projectId);
  void fetch(`/api/context/draft?${qs}`)
    .then((response) => response.json() as Promise<ContextBreakdown>)
    .then((data) => {
      if (latestRequest.get(key) === token && !data.error) touch(key, data);
    })
    .catch(() => undefined)
    .finally(() => {
      if (latestRequest.get(key) === token) latestRequest.delete(key);
    });
}
