"use client";

/** The roster facts of one conversation, read while the overview shows:
 *  on entry, when an execution or a Job moves, and every 30 seconds. Reads
 *  never overlap and follow each other at least a second apart, so a busy
 *  turn does not turn into a read per step. The conversation's last read
 *  draws at once, and entry does not read again while it is fresh
 *  (activity-reads.ts). */
import { useEffect, useState } from "react";
import { getActivityRoster, type ActivityRoster } from "@/lib/net/execution/activity-roster-client";
import { activityReadIsFresh, lastActivityRead, activityReadStart, rememberActivityRead } from "@/lib/execution/activity-reads";

const POLL_MS = 30000;
const GAP_MS = 1000;
const EMPTY: ActivityRoster = { agents: {}, outgoing: [] };

export function useAgentRoster(active: boolean, sessionId: string | null): ActivityRoster {
  const [held, setHeld] = useState<{ sessionId: string; roster: ActivityRoster } | null>(null);
  useEffect(() => {
    if (!active || !sessionId) return;
    const abort = new AbortController();
    let reading = false, dirty = false, alive = true;
    let wait: ReturnType<typeof setTimeout> | null = null;
    const read = async () => {
      if (reading || wait) { dirty = true; return; }
      reading = true; dirty = false;
      const startedAt = activityReadStart();
      try {
        const roster = await getActivityRoster(sessionId, abort.signal);
        const read = { agents: roster.agents ?? {}, outgoing: roster.outgoing ?? [] };
        rememberActivityRead("roster", sessionId, read, startedAt);
        if (alive) setHeld({ sessionId, roster: read });
      } catch { /* the rows show without their facts until the next read */ }
      reading = false;
      if (!alive) return;
      wait = setTimeout(() => { wait = null; if (dirty) void read(); }, GAP_MS);
    };
    const onUpdate = () => { void read(); };
    if (!activityReadIsFresh("roster", sessionId)) void read();
    const timer = setInterval(onUpdate, POLL_MS);
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    return () => {
      alive = false; abort.abort(); clearInterval(timer);
      if (wait) clearTimeout(wait);
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
    };
  }, [active, sessionId]);
  return held && held.sessionId === sessionId ? held.roster : lastActivityRead<ActivityRoster>("roster", sessionId) ?? EMPTY;
}
