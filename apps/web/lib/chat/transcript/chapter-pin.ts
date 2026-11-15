/**
 * Pin or unpin a turn as a chapter over the shared WebSocket.
 *
 *   send:  {action: "set_message_chapter", session_id, message_id, pinned, request_id}
 *   reply: the broadcast {type: "message_chapter_updated",
 *          data: {session_id, message_id, chapter: {pinned_at} | null, request_id, action}}
 *
 * The requesting window takes the broadcast as its reply; every window gets
 * the pin as a row update. The row shows the pin at once and goes back if the
 * server refuses.
 */
import { useSessionStore } from "@/lib/session-store";
import { wsRequest } from "@/lib/net/ws/ws-request";

export type ChapterFrame = {
  session_id?: unknown;
  message_id?: unknown;
  chapter?: unknown;
  status?: string;
};

/** Pin (`pinned` true) or unpin the turn `messageId` (a user message)
 *  starts. Resolves true when the server stored it. */
export async function setTurnChapter(
  sessionId: string,
  messageId: string,
  pinned: boolean,
): Promise<boolean> {
  const store = useSessionStore.getState();
  store.applyLocalChange(sessionId, {
    kind: "overlay_set", rowId: messageId,
    fields: { chapterPinnedAt: pinned ? Date.now() / 1000 : undefined },
    clear: ["until_field_matches"],
  });
  const reply = await wsRequest<ChapterFrame>(
    "set_message_chapter",
    { session_id: sessionId, message_id: messageId, pinned },
    "message_chapter_updated",
    { requestId: true },
  );
  // The pin time is the server's, so the row can never match this browser's guess.
  useSessionStore.getState().applyLocalChange(sessionId, { kind: "overlay_clear", rowId: messageId });
  return !!reply && reply.status !== "error";
}
