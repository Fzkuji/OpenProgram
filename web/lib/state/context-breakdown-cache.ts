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
interface CacheEntry {
  value?: ContextBreakdown;
  generation: number;
}
const cache = new Map<string, CacheEntry>();

function cacheKey(sessionId: string | null, headId?: string | null): string | null {
  return sessionId ? JSON.stringify([sessionId, headId ?? null]) : null;
}

function touch(key: string, entry: CacheEntry): void {
  cache.delete(key);
  cache.set(key, entry);
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
  const entry = cache.get(key);
  if (!entry?.value) return null;
  touch(key, entry);
  return entry.value;
}

export function writeContextBreakdownCache(
  sessionId: string,
  headId: string | null | undefined,
  value: ContextBreakdown,
): void {
  const key = JSON.stringify([sessionId, headId ?? null]);
  const entry = cache.get(key) ?? { generation: 0 };
  entry.value = value;
  touch(key, entry);
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
  const entry = cache.get(key) ?? { generation: 0 };
  const generation = ++entry.generation;
  touch(key, entry);
  const isCurrent = () => cache.get(key)?.generation === generation;
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
  }
}
