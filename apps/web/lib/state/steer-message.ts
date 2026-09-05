"use client";

import { wsRequest } from "@/lib/net/ws-request";
import { queueFor, useSendQueue } from "@/lib/state/send-queue";

interface SteerAck {
  session_id?: string;
  request_id?: string;
  result?: "accepted" | "not_running";
}

/** Try to inject one existing queued row. The row remains the fallback. */
export async function steerQueuedMessage(
  sessionId: string,
  messageId: string,
): Promise<boolean> {
  const message = queueFor(sessionId).find((item) => item.id === messageId);
  if (!message || message.injecting) return false;

  const queue = useSendQueue.getState();
  queue.setInjecting(sessionId, messageId, true);
  const requestId = `steer_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const ack = await wsRequest<SteerAck>(
    "steer",
    { session_id: sessionId, message: message.text, request_id: requestId },
    "steer_ack",
    (data) => data.request_id === requestId && data.session_id === sessionId,
  );

  if (ack?.result === "accepted") {
    useSendQueue.getState().remove(sessionId, messageId);
    return true;
  }

  useSendQueue.getState().setInjecting(sessionId, messageId, false);
  if (ack?.result === "not_running") {
    useSendQueue.getState().drain(sessionId);
  }
  return false;
}
