"use client";

/**
 * Session-goal indicator — "◎ goal · N/M" in the composer's env-chip
 * row while the session has an active /goal.
 *
 * Data sources (no store schema changes):
 *   - hydration: `session_loaded.data.goal` lands on
 *     `runtimeState.conversations[sid].goal` (loadSessionData merges all
 *     payload fields) and is re-announced as an `op:goal-state` window
 *     event by conversations.ts;
 *   - live: the backend broadcasts top-level `goal_update` frames, which
 *     use-ws's catch-all re-dispatches as `op:ws-message` events.
 */

import { useEffect, useState } from "react";
import { Target } from "lucide-react";

import { runtimeState } from "@/lib/runtime-bridge/state";
import { useSessionStore } from "@/lib/session-store";

interface GoalState {
  text?: string;
  status?: string;
  turns_used?: number;
  max_turns?: number;
  last_reason?: string;
}

function readGoalFromRuntime(sid: string | null): GoalState | null {
  if (!sid) return null;
  const conv = runtimeState.conversations[sid] as
    | { goal?: GoalState | null }
    | undefined;
  return conv?.goal ?? null;
}

export function GoalChip() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [goal, setGoal] = useState<GoalState | null>(() =>
    readGoalFromRuntime(sessionId),
  );

  // Session switch → re-read whatever the runtime bridge already has
  // (may be null until session_loaded lands; op:goal-state fills it in).
  useEffect(() => {
    setGoal(readGoalFromRuntime(sessionId));
  }, [sessionId]);

  useEffect(() => {
    const onGoalState = (e: Event) => {
      const d = (e as CustomEvent).detail as
        | { session_id?: string; goal?: GoalState | null }
        | undefined;
      if (!d?.session_id) return;
      if (d.session_id === useSessionStore.getState().currentSessionId) {
        setGoal(d.goal ?? null);
      }
    };
    const onWsMessage = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { type?: string; data?: { session_id?: string; goal?: GoalState } }
        | undefined;
      if (detail?.type !== "goal_update") return;
      const d = detail.data;
      if (!d?.session_id) return;
      if (d.session_id === useSessionStore.getState().currentSessionId) {
        setGoal(d.goal ?? null);
      }
    };
    window.addEventListener("op:goal-state", onGoalState);
    window.addEventListener("op:ws-message", onWsMessage);
    return () => {
      window.removeEventListener("op:goal-state", onGoalState);
      window.removeEventListener("op:ws-message", onWsMessage);
    };
  }, []);

  // 只在 goal 进行中显示；没设目标或已终结（achieved/cleared/capped/
  // error）就不占底栏。
  const active = goal?.status === "active";
  const waiting = goal?.status === "waiting_user";
  if (!goal || (!active && !waiting)) return null;

  const label = active
    ? `goal · ${goal.turns_used ?? 0}${goal.max_turns ? `/${goal.max_turns}` : ""}`
    : "goal · 等你回答";
  const tip = [
    goal.text,
    waiting ? (goal as { last_question?: string }).last_question : null,
    goal.last_reason,
  ].filter(Boolean).join(" — ");
  return (
    <span className="runtime-badge workdir-badge" title={tip || undefined}>
      <Target size={14} strokeWidth={2} className="workdir-icon" />
      <span className="badge-short">{label}</span>
    </span>
  );
}
