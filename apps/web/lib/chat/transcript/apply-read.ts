/** Apply a transcript read to a view, and settle the steers it delivered. */
import { settleDeliveredSteer } from "@/lib/chat/send/send-queue";
import { isSubAgentChatKey } from "@/lib/chat/transcript/sub-agent-keys";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";

/** `whole` says this read replaced the window, not one page of it. */
export function applyReadToView(view: string, rows: ChatMsg[], whole = true): void {
  const delivered = useSessionStore.getState().applyRead(view, rows, whole);
  if (isSubAgentChatKey(view)) return;
  for (const commandId of delivered) settleDeliveredSteer(view, commandId);
}

/** A notice this browser adds to a view, drawn after the row now last. */
export function appendLocalNotice(view: string, row: Omit<ChatMsg, "rowKind"> & { rowKind: "system_text" | "system_event" }): void {
  const store = useSessionStore.getState();
  store.applyLocalChange(view, { kind: "local_append", row, afterRowId: (store.messageOrder[view] ?? []).at(-1) ?? null });
}
