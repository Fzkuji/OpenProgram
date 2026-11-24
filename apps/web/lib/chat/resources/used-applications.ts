"use client";

import { create } from "zustand";
import { APPLICATION_TARGET, followAppTarget } from "./session-resources.ts";

/** The built-in application an Agent last used in a conversation, as the
 *  worker announces it (`application.used`), so the chat preview follows it
 *  the way it follows pages, app windows and terminals. */
export type UsedApplication = {
  sessionId: string;
  applicationId: string;
  instanceId: string;
  title: string;
  sequence: number;
};

export const useUsedApplications = create<{ applications: Record<string, UsedApplication> }>(() => ({
  applications: {},
}));

export function ingestUsedApplication(data: unknown): UsedApplication | null {
  if (!data || typeof data !== "object") return null;
  const item = data as Record<string, unknown>;
  if (typeof item.session_id !== "string" || !item.session_id
      || typeof item.application_id !== "string" || !item.application_id
      || typeof item.instance_id !== "string" || !item.instance_id
      || !Number.isInteger(item.sequence)) return null;
  const next: UsedApplication = {
    sessionId: item.session_id,
    applicationId: item.application_id,
    instanceId: item.instance_id,
    title: typeof item.title === "string" ? item.title : "",
    sequence: item.sequence as number,
  };
  const previous = useUsedApplications.getState().applications[next.sessionId];
  // Events can arrive twice or out of order across reconnects.
  if (previous && previous.sequence >= next.sequence) return previous;
  useUsedApplications.setState(state => ({ applications: { ...state.applications, [next.sessionId]: next } }));
  followAppTarget(next.sessionId, `${APPLICATION_TARGET}${next.instanceId}`);
  return next;
}

if (typeof window !== "undefined") {
  window.addEventListener("op:ws-message", (event) => {
    const detail = (event as CustomEvent<{ type?: string; data?: unknown }>).detail;
    if (detail?.type === "application.used") ingestUsedApplication(detail.data);
  });
}
