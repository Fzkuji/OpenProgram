/** Small pure helpers shared by the timeline subcomponents. */

import type { CommitMeta } from "./types";

export function fmtRelTime(ts: number): string {
  const now = Date.now() / 1000;
  const d = Math.max(0, now - ts);
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  return `${Math.round(d / 86400)}d ago`;
}

/** Fire-and-forget WS send. No-op if the socket isn't connected;
 *  the caller re-fires on reconnect via the auto-refresh path. */
export function wsSend(obj: unknown): void {
  const w = window as unknown as { ws?: WebSocket | null };
  const sock = w.ws;
  if (sock && sock.readyState === WebSocket.OPEN) {
    sock.send(JSON.stringify(obj));
  }
}

/** Bucket commits sharing a turn_group_id into one row. Preserves
 *  the order commits arrive in (newest-first per backend); attempts
 *  inside a group are sorted oldest→newest so the switcher numbers
 *  ascend chronologically. */
export function groupCommits(
  commits: CommitMeta[],
): Array<{ id: string; attempts: CommitMeta[] }> {
  const out: Array<{ id: string; attempts: CommitMeta[] }> = [];
  const idx = new Map<string, number>();
  for (const c of commits) {
    let i = idx.get(c.turn_group_id);
    if (i === undefined) {
      i = out.length;
      idx.set(c.turn_group_id, i);
      out.push({ id: c.turn_group_id, attempts: [] });
    }
    out[i].attempts.push(c);
  }
  for (const g of out) g.attempts.sort((a, b) => a.created_at - b.created_at);
  return out;
}
