"use client";

/** The roster facts of one conversation, read while the overview shows:
 *  on entry, when an execution or a Job moves, and when the Worker says the
 *  conversation's Jobs (`runs`) or what its helpers used (`roster`) changed
 *  (lib/net/ws/changes.ts). Reads
 *  never overlap and follow each other at least a second apart, so a busy
 *  turn does not turn into a read per step. The conversation's last read
 *  draws at once, and entry does not read again while it is fresh
 *  (activity-reads.ts). */
import { useEffect, useRef, useState } from "react";
import { getActivityRoster, type ActivityRoster } from "@/lib/net/execution/activity-roster-client";
import { activityReadIsFresh, lastActivityRead, activityReadStart, rememberActivityRead } from "@/lib/execution/activity-reads";
import { useChanged } from "@/lib/net/ws/changes";

/** Changes that arrive in a burst are read once, a second after the last read. */
const GAP_MS = 1000;
/** A failed read is tried again after 2, 4, 8 … seconds, up to 30. */
const RETRY_MAX_MS = 30000;
const EMPTY: ActivityRoster = { agents: {}, outgoing: [] };

export function useAgentRoster(active: boolean, sessionId: string | null): ActivityRoster {
  const [held, setHeld] = useState<{ sessionId: string; roster: ActivityRoster } | null>(null);
  const reader = useRef<(() => void) | null>(null);
  useChanged(["runs", "roster"], () => reader.current?.(), sessionId ? [sessionId] : []);
  useEffect(() => {
    if (!active || !sessionId) return;
    const abort = new AbortController();
    let reading = false, dirty = false, alive = true, failures = 0, lastAt = 0;
    // Set only while a change waits out the gap after the last read, or a
    // failed read waits to be tried again; idle, nothing is scheduled.
    let wait: ReturnType<typeof setTimeout> | null = null;
    const later = (ms: number) => { wait = setTimeout(() => { wait = null; void read(); }, ms); };
    const read = async () => {
      if (reading) { dirty = true; return; }
      if (wait) return;
      const gap = GAP_MS - (Date.now() - lastAt);
      if (gap > 0) { later(gap); return; }
      reading = true; dirty = false; lastAt = Date.now();
      const startedAt = activityReadStart();
      let failed = false;
      try {
        const roster = await getActivityRoster(sessionId, abort.signal);
        const read = { agents: roster.agents ?? {}, outgoing: roster.outgoing ?? [] };
        rememberActivityRead("roster", sessionId, read, startedAt);
        failures = 0;
        if (alive) setHeld({ sessionId, roster: read });
      } catch (error) {
        // The rows keep the facts last read and the read is tried again soon.
        if (alive) console.warn("Activity: could not read the roster", error);
        failures += 1;
        failed = true;
      }
      reading = false;
      if (!alive) return;
      if (failed) later(Math.min(RETRY_MAX_MS, 1000 * 2 ** failures));
      else if (dirty) void read();
    };
    const onUpdate = () => { void read(); };
    if (!activityReadIsFresh("roster", sessionId)) void read();
    reader.current = onUpdate;
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    return () => {
      alive = false; abort.abort();
      if (reader.current === onUpdate) reader.current = null;
      if (wait) clearTimeout(wait);
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
    };
  }, [active, sessionId]);
  return held && held.sessionId === sessionId ? held.roster : lastActivityRead<ActivityRoster>("roster", sessionId) ?? EMPTY;
}
