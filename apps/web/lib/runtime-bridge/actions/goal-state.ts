import { runtimeState } from "../state";
import { selectRow, useSessionStore } from "../../session-store";
import type { GoalVerification } from "../../chat/decisions/goal-verification";

/** Replay a snapshot that may have arrived before its assistant row. */
export function verificationForMessage(
  sessionId: string, messageId: string, incoming: GoalVerification,
  current?: GoalVerification,
): GoalVerification {
  const goal = runtimeState.conversations[sessionId]?.goal as {
    verification_message?: { message_id: string; presentation: GoalVerification } | null;
  } | undefined;
  const result = goal?.verification_message;
  if (result?.message_id === messageId && result.presentation.id === incoming.id
      && result.presentation.status !== "pending") {
    return result.presentation;
  }
  return current?.id === incoming.id && current.status !== "pending" ? current : incoming;
}

/** Apply HTTP, hydration and live snapshots in durable session-version order. */
export function updateSessionGoal(
  sessionId: string,
  goal: { version?: number; verification_message?: { message_id: string; presentation: GoalVerification } | null } | null,
): void {
  const conversation = runtimeState.conversations[sessionId] ?? { id: sessionId };
  const previous = conversation.goal as { version?: number } | null | undefined;
  if ((previous?.version ?? 0) > (goal?.version ?? 0)) return;
  conversation.goal = goal;
  runtimeState.conversations[sessionId] = conversation;
  const result = goal?.verification_message;
  if (result && selectRow(sessionId, result.message_id)(useSessionStore.getState())) {
    useSessionStore.getState().applyLocalChange(sessionId, {
      kind: "overlay_set", rowId: result.message_id,
      fields: { goalVerification: result.presentation }, clear: ["until_field_matches"],
    });
  }
  window.dispatchEvent(new CustomEvent("op:goal-state", {
    detail: { session_id: sessionId, goal },
  }));
}
