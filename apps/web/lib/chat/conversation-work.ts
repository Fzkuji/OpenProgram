"use client";

// What a conversation keeps doing on its own, for its Activity panel: the
// always-on state its Agent's configuration gives it, its open work, and
// the Scheduler tasks set from it (GET /api/sessions/{id}/work).
import { useCallback, useEffect, useRef, useState } from "react";
import { jsonFetch } from "@/lib/net/fetch-client";
import type {
  AlwaysOnDelivery, AlwaysOnNotify, AlwaysOnQuietHours, AlwaysOnReportKind, AlwaysOnWakeOutcome,
} from "@/lib/agents/always-on";

export type WakeHealth = {
  last_at: number; wakes_today: number; failed_today: number; failed_in_a_row: number;
};

export type OpenWorkItem = {
  id: string;
  text: string;
  note: string;
  status: "open" | "done";
  created_at: number;
  updated_at: number;
  done_at: number | null;
};

export type ConversationReport = {
  id: string;
  at: number;
  title: string;
  body: string;
  links: string[];
  /** Absent on reports made before kinds existed: a finding. */
  kind?: AlwaysOnReportKind;
  deliveries?: AlwaysOnDelivery[];
  /** False when it was saved but never posted to the conversation. */
  in_chat?: boolean;
};

/** A rule the user gave the Agent about what to report. */
export type ReportPreference = { id: string; text: string; at: number };

/** What one check-in used, when known. */
export type WakeCost = { input_tokens?: number | null; output_tokens?: number | null; usd?: number | null };

export type ConversationAlwaysOn = {
  enabled: boolean;
  agent_id: string | null;
  agent_name: string | null;
  config: {
    goal: string;
    /** null: the agent schedules its own wakes. */
    wake_minutes: number | null;
    research: boolean; autonomy: "report" | "act";
    quiet_hours?: AlwaysOnQuietHours | null; wake_days?: number[];
    check_in_model?: string; notify?: AlwaysOnNotify; daily_budget_usd?: number | null;
  } | null;
  running: boolean;
  goal: { text: string; status: string } | null;
  last_wake: {
    at: number; reason: string; outcome: AlwaysOnWakeOutcome;
    /** Why an interrupted check-in stopped. */
    error?: string | null;
    cost?: WakeCost | null;
  } | null;
  /** The wake log over the last day; null before the first wake. */
  wake_health?: WakeHealth | null;
  /** Already moved past quiet hours and days without wakes. */
  next_wake_at: number | null;
  /** What the agent said it will look at, when it scheduled the next wake itself. */
  next_wake_reason?: string | null;
  /** This Agent's check-in spending today, in dollars. */
  today_cost_usd?: number | null;
  preferences?: ReportPreference[];
  /** Scheduled wakes are held back right now. */
  quiet_now?: boolean;
  reports: ConversationReport[];
};

export type RoutineRun = {
  at: number;
  status: "quiet" | "result" | "failed" | "skipped" | "missed";
  text?: string;
  tip?: string | null;
};

export type Routine = {
  id: string;
  title: string;
  type: "once" | "recurring" | "monitor" | "webhook";
  enabled: boolean;
  created_at: number;
  cron?: string;
  run_at?: string;
  prompt?: string;
  command?: string;
  reminder?: string;
  watch?: string;
  notes?: string;
  hook_key?: string;
  hook_url?: string;
  /** The last runs of a task that runs a prompt, newest first. */
  runs?: RoutineRun[];
};

export type ConversationWork = {
  session_id: string;
  always_on: ConversationAlwaysOn;
  open_work: OpenWorkItem[];
  routines: Routine[];
};

const base = (sessionId: string) => `/api/sessions/${encodeURIComponent(sessionId)}`;

export function wakeConversation(sessionId: string): Promise<{ outcome: AlwaysOnWakeOutcome }> {
  return jsonFetch(`${base(sessionId)}/always-on/wake`, { method: "POST" });
}

/** Teach the conversation's Agent what (not) to report. */
export function addReportPreference(sessionId: string, text: string): Promise<ReportPreference> {
  return jsonFetch(`${base(sessionId)}/always-on/preferences`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
  });
}

export function removeReportPreference(sessionId: string, preferenceId: string): Promise<{ ok: boolean }> {
  return jsonFetch(`${base(sessionId)}/always-on/preferences/${encodeURIComponent(preferenceId)}`, { method: "DELETE" });
}

/** The preference "Don't report things like this" records for a report. */
export function dontReportText(title: string): string {
  return `Don't report: ${title}`;
}

export function setOpenWorkStatus(sessionId: string, itemId: string, status: OpenWorkItem["status"]): Promise<OpenWorkItem> {
  return jsonFetch(`${base(sessionId)}/open-work/${encodeURIComponent(itemId)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
  });
}

export function removeOpenWork(sessionId: string, itemId: string): Promise<{ ok: boolean }> {
  return jsonFetch(`${base(sessionId)}/open-work/${encodeURIComponent(itemId)}`, { method: "DELETE" });
}

const listeners = new Set<(sessionId: string) => void>();

/** The worker said this conversation's always-on state, reports or open work changed. */
export function notifyConversationWorkChanged(sessionId: string): void {
  for (const listener of listeners) listener(sessionId);
}

/** Calls ``listener`` on every change frame, for any conversation; returns the unsubscribe. */
export function onConversationWorkChanged(listener: (sessionId: string) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Reads the conversation's work while the panel is open: on open, on the
 *  worker's change frames, on reconnect and focus, and every 30 seconds
 *  for Scheduler tasks, which publish no event of their own. */
export function useConversationWork(active: boolean, sessionId: string | null) {
  const [work, setWork] = useState<ConversationWork | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => { setWork(null); }, [sessionId]);
  useEffect(() => {
    if (!active || !sessionId) return;
    let disposed = false;
    let controller: AbortController | null = null;
    const load = () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      const current = new AbortController();
      controller = current;
      jsonFetch<ConversationWork>(`${base(sessionId)}/work`, { signal: current.signal })
        .then(data => { if (!disposed) setWork(data); })
        .catch(() => { /* keep the last read; the next signal retries */ });
    };
    refreshRef.current = load;
    const onChange = (changed: string) => { if (changed === sessionId) load(); };
    listeners.add(onChange);
    const timer = setInterval(load, 30000);
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", load);
    load();
    return () => {
      disposed = true;
      refreshRef.current = () => {};
      controller?.abort();
      listeners.delete(onChange);
      clearInterval(timer);
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", load);
    };
  }, [active, sessionId]);
  return { work, refresh };
}
