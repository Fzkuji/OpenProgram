"use client";

import { useSessionStore } from "@/lib/session-store";

import { disabledCause, forkDisabledCause, isLastPrompt, type DisabledCause, type ForkDisabledCause } from "./rewind-logic";
import { useRewindStore } from "./rewind-store";

/** Why this conversation cannot rewind right now, kept current. */
export function useRewindDisabled(sessionId: string | null | undefined): DisabledCause | null {
  const running = useSessionStore(s => Boolean(sessionId && s.runningTasks?.[sessionId]));
  const offline = useSessionStore(s => s.wsStatus === "closed");
  const uploading = useRewindStore(s => Boolean(sessionId && s.uploading[sessionId]));
  return disabledCause({ running, offline, uploading });
}

/** Whether rewinding exists at all for this conversation. */
export function useRewindAllowed(sessionId: string | null | undefined): boolean {
  return useSessionStore(s => !(sessionId && s.conversations?.[sessionId]?.archived));
}

/** Why this message cannot be forked right now: a copy needs the server and
 *  a finished reply. One read of the answer, so a run starting or ending
 *  redraws only the last prompt's actions, the one message it changes. */
export function useForkDisabled(sessionId: string | null | undefined, msgId: string): ForkDisabledCause | null {
  return useSessionStore(s => {
    const offline = s.wsStatus === "closed";
    const running = Boolean(sessionId && s.runningTasks?.[sessionId]);
    return forkDisabledCause({
      offline,
      running,
      lastPrompt: running && !offline && isLastPrompt(s.messageOrder?.[sessionId!], s.rowsByView?.[sessionId!] ?? {}, msgId),
    });
  });
}
