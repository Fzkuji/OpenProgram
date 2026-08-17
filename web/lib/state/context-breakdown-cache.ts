export interface ContextBreakdown {
  messages?: number;
  system_prompt?: number;
  skills?: number;
  memory?: number;
  tools_schema?: number;
  tools_deferred_catalog?: number;
  mcp_tools?: number;
  mcp_tools_deferred?: number;
  input_used?: number;
  free_space?: number;
  total_used?: number;
  window?: number;
  basis?: string;
  tools?: Array<{ name: string; tokens: number; deferred: boolean }>;
  skills_detail?: Array<{ name: string; source: string; tokens: number }>;
  memory_detail?: Array<{ path: string; tokens: number }>;
  mcp_detail?: Array<{ server: string; name: string; tokens: number; deferred: boolean }>;
  context_window?: number;
  model?: string;
  error?: string;
}

const CACHE_LIMIT = 32;
const cache = new Map<string, ContextBreakdown>();
const latestRequest = new Map<string, symbol>();

function cacheKey(sessionId: string | null, headId?: string | null): string | null {
  return sessionId ? JSON.stringify([sessionId, headId ?? null]) : null;
}

function touch(key: string, value: ContextBreakdown): void {
  cache.delete(key);
  cache.set(key, value);
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

export function readContextBreakdownCache(
  sessionId: string | null,
  headId?: string | null,
): ContextBreakdown | null {
  const key = cacheKey(sessionId, headId);
  if (!key) return null;
  const value = cache.get(key);
  if (!value) return null;
  touch(key, value);
  return value;
}

export function writeContextBreakdownCache(
  sessionId: string,
  headId: string | null | undefined,
  value: ContextBreakdown,
): void {
  const key = JSON.stringify([sessionId, headId ?? null]);
  touch(key, value);
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
  const cached = readContextBreakdownCache(sessionId, headId);
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
