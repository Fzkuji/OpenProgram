"use client";

/**
 * Messages typed during a run that are on their way into it. They show at
 * the end of the running reply straight away, as pending user bubbles, where
 * the turn will add them at its next safe point; the queue card above the
 * input box keeps only messages that wait for the next turn or failed. When
 * the turn writes the message, its real steering block takes this place.
 */

import { steeringIntoTurn, useSendQueue, type QueuedMessage } from "@/lib/chat/send/send-queue";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { UserBubble } from "./user-bubble";

const EMPTY: QueuedMessage[] = [];

export function PendingSteers({ sessionId, replyId }: { sessionId: string; replyId: string }) {
  const rows = useSendQueue(s => s.queues[sessionId] ?? EMPTY);
  // The running task names its turn's reply row as the server does.
  const ownsTurn = useSessionStore(s => {
    const task = s.runningTasks[sessionId];
    return Boolean(task?.msg_id) && (task?.reply_id === replyId || task?.msg_id === replyId);
  });
  if (!ownsTurn) return null;
  return <>
    {rows.filter(steeringIntoTurn).map(row => {
      const message: ChatMsg = {
        id: `queued:${row.id}`, role: "user", rowKind: "user", content: row.text, steering: true, status: "pending",
        pastedElements: row.textElements,
      };
      return <div key={row.id} className="steering-input" data-pending-steer={row.id}>
        <UserBubble msg={message} sessionIdOverride={sessionId} />
      </div>;
    })}
  </>;
}
