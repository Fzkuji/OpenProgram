/** Sends written to the socket that have no `chat_ack` yet. */
import {
  clearPendingFirstAck,
  clearPendingUserText,
  getPendingUserRequestId,
  hasPendingUserText,
} from "@/lib/chat/send/pending-user-text";
import { selectRow, useSessionStore } from "@/lib/session-store";

/** Whether a frame answers the send waiting for its ACK in `sid`. A frame
 *  echoing a `client_request_id` answers only that request; one naming none
 *  answers the session's pending send, as older servers' frames do. */
export function frameNamesPendingSend(
  sid: string,
  frame: object | undefined,
): boolean {
  const echoed = (frame as { client_request_id?: unknown } | undefined)?.client_request_id;
  if (typeof echoed !== "string" || !echoed) return true;
  return echoed === getPendingUserRequestId(sid);
}

/** The optimistic row of a send that never reached the backend says so. */
export function markSendNotSent(sid: string, rowId: string | undefined, reason = "not_sent"): void {
  const store = useSessionStore.getState();
  if (!rowId || selectRow(sid, rowId)(store)?.status !== "pending") return;
  store.applyLocalChange(sid, { kind: "local_patch", rowId, fields: { status: "error", rawType: "error", errorReason: reason } });
}

/** A closed socket ends every send still waiting for its ACK: the ACK can
 *  only come on the socket that carried the send. Their rows show "Not
 *  sent"; a later load drops any the backend did accept. */
export function settleSendsOnDisconnect(): void {
  const store = useSessionStore.getState();
  for (const [sid, ids] of Object.entries(store.messageOrder)) {
    const pending = ids.filter((id) => {
      const row = selectRow(sid, id)(store);
      return row?.role === "user" && row.status === "pending";
    });
    if (!pending.length && !hasPendingUserText(sid)) continue;
    for (const id of pending) markSendNotSent(sid, id, "disconnected");
    clearPendingUserText(sid);
    clearPendingFirstAck(sid);
  }
}
