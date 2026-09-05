import { runtimeState } from "./state";

/** Apply HTTP, hydration and live snapshots in durable session-version order. */
export function updateSessionGoal(
  sessionId: string,
  goal: { version?: number } | null,
): void {
  const conversation = runtimeState.conversations[sessionId] ?? { id: sessionId };
  const previous = conversation.goal as { version?: number } | null | undefined;
  if ((previous?.version ?? 0) > (goal?.version ?? 0)) return;
  conversation.goal = goal;
  runtimeState.conversations[sessionId] = conversation;
  window.dispatchEvent(new CustomEvent("op:goal-state", {
    detail: { session_id: sessionId, goal },
  }));
}
