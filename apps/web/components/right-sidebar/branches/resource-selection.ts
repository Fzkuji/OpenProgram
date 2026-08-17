import type { JobResourceView } from "@/lib/net/ws-events";

export interface JobResourceEntry {
  targetHead?: string | null;
  finalHead?: string | null;
  status: string;
  resource?: JobResourceView | null;
  updatedAt: number;
}

const TERMINAL = new Set(["completed", "cancelled", "errored"]);

export function selectResourceForHead(
  jobMap: Record<string, JobResourceEntry>,
  headId: string,
  pendingPrefix: string,
): JobResourceView | null {
  const matches = Object.entries(jobMap).filter(([jobId, entry]) => {
    const mapped = entry.finalHead || entry.targetHead || `${pendingPrefix}${jobId}`;
    return mapped === headId;
  });
  matches.sort((a, b) => {
    const aTerminal = TERMINAL.has(a[1].status) ? 1 : 0;
    const bTerminal = TERMINAL.has(b[1].status) ? 1 : 0;
    return aTerminal - bTerminal || b[1].updatedAt - a[1].updatedAt;
  });
  return matches[0]?.[1].resource || null;
}
