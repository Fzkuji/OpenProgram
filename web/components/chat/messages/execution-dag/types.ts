/**
 * Shared types + helpers for the execution-DAG view.
 *
 * Pulled out of execution-dag.tsx so the recursive TreeNodeRow + the
 * separated RetryPanel can both import the same shape without
 * circular references back to the main component.
 */

export interface TNode {
  path?: string;
  name?: string;
  status?: string;
  node_type?: string;
  params?: Record<string, unknown>;
  output?: unknown;
  raw_reply?: string;
  duration_ms?: number;
  start_time?: number;
  end_time?: number;
  error?: string;
  children?: TNode[];
}

/** Send a JSON payload through the legacy `window.ws` socket. */
export function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
  return true;
}

/** Recursively collect every node's ``path`` into ``set``. */
export function collectPaths(node: TNode, set: Set<string>): void {
  if (node.path) set.add(node.path);
  if (node.children) for (const c of node.children) collectPaths(c, set);
}

/** A node is "running" only if its status says so AND it hasn't
 *  recorded an end — mirrors the legacy ``_treeHasRunning`` race
 *  guard so a late status update doesn't keep the tree looking busy
 *  after the duration / end_time has been written. */
export function treeHasRunning(node: TNode | undefined): boolean {
  if (!node) return false;
  const ended =
    (!!node.duration_ms && node.duration_ms > 0) ||
    (!!node.end_time && node.end_time > 0);
  if (node.status === "running" && !ended) return true;
  return (node.children ?? []).some(treeHasRunning);
}

/** Params keys we never surface in the retry form / Copy-JSON payload. */
const PARAM_SKIP = new Set(["runtime", "callback"]);

/** Strip runtime-only params before showing them to the user. */
export function filteredParams(params: Record<string, unknown> | undefined) {
  const out: Record<string, unknown> = {};
  if (params) {
    for (const k of Object.keys(params)) {
      if (!PARAM_SKIP.has(k)) out[k] = params[k];
    }
  }
  return out;
}

/** Strip ``children`` + runtime params for the Copy-JSON payload. */
export function cleanForCopy(node: TNode): unknown {
  const c: Record<string, unknown> = {};
  for (const k of Object.keys(node)) {
    if (k === "children") continue;
    if (k === "params") {
      c.params = filteredParams(node.params);
    } else {
      c[k] = (node as Record<string, unknown>)[k];
    }
  }
  if (node.children && node.children.length) {
    c.children = node.children.map(cleanForCopy);
  }
  return c;
}

/** Flatten params into dotted-key string fields, matching the legacy
 *  ``_buildRetryFields`` so ``executeRetry``'s ``key.split(".")``
 *  rebuild works unchanged. */
export function flattenParams(
  params: Record<string, unknown>,
  prefix: string,
  out: { key: string; value: string; long: boolean }[],
): void {
  for (const k of Object.keys(params)) {
    if (PARAM_SKIP.has(k)) continue;
    const v = params[k];
    const fullKey = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      flattenParams(v as Record<string, unknown>, fullKey, out);
    } else {
      const vs = typeof v === "string" ? v : JSON.stringify(v);
      out.push({
        key: fullKey,
        value: vs,
        long: vs.length > 60 || vs.includes("\n"),
      });
    }
  }
}

/** Truncate a string with an ellipsis. Shared by tree-row labels. */
export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
