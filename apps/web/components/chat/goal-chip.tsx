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

export interface GoalState {
  text?: string;
  status?: string;
  turns_used?: number;
  max_turns?: number;
  checklist?: { text: string; done: boolean }[] | null;
  last_reason?: string;
  last_question?: string;
  last_question_at?: number;
  last_question_options?: { label: string; description: string }[];
}

function readGoalFromRuntime(sid: string | null): GoalState | null {
  if (!sid) return null;
  const conv = runtimeState.conversations[sid] as
    | { goal?: GoalState | null }
    | undefined;
  return conv?.goal ?? null;
}

/** Live goal state for ONE session — hydration read + the two event
 *  subscriptions (op:goal-state re-announce, goal_update WS frames).
 *  Shared by the env-chip (GoalChip) and the waiting_user question card
 *  (composer/goal-question-card.tsx). */
export function useSessionGoal(sessionId: string | null): GoalState | null {
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
      if (d.session_id === sessionId) {
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
      if (d.session_id === sessionId) {
        setGoal(d.goal ?? null);
      }
    };
    window.addEventListener("op:goal-state", onGoalState);
    window.addEventListener("op:ws-message", onWsMessage);
    return () => {
      window.removeEventListener("op:goal-state", onGoalState);
      window.removeEventListener("op:ws-message", onWsMessage);
    };
  }, [sessionId]);

  return goal;
}

export function GoalChip() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const goal = useSessionGoal(sessionId);

  // 只在 goal 进行中显示；没设目标或已终结（achieved/cleared/capped/
  // error）就不占底栏。
  const active = goal?.status === "active";
  const waiting = goal?.status === "waiting_user";
  if (!goal || (!active && !waiting)) return null;

  // Checklist progress beats turn count when the refinement produced
  // acceptance items — "goal · done/total" reads as real progress.
  const checklist = goal.checklist ?? [];
  const label = active
    ? checklist.length
      ? `goal · ${checklist.filter((it) => it.done).length}/${checklist.length}`
      : `goal · ${goal.turns_used ?? 0}${goal.max_turns ? `/${goal.max_turns}` : ""}`
    : "goal · 等你回答";
  const tip = [
    goal.text,
    waiting ? goal.last_question : null,
    goal.last_reason,
  ].filter(Boolean).join(" — ");
  return (
    <span className="runtime-badge workdir-badge" title={tip || undefined}>
      <Target size={14} strokeWidth={2} className="workdir-icon" />
      <span className="badge-short">{label}</span>
    </span>
  );
}
