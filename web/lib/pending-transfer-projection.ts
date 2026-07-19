import type { TransferJournalEntry } from "@/lib/tab-transfer-journal";

const entriesByWindow = new Map<string, Map<string, TransferJournalEntry>>();

function desktopWindowId(): string | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as unknown as {
    openprogramDesktop?: { isDesktop?: boolean; windowId?: string };
  }).openprogramDesktop;
  if (!bridge?.isDesktop) return null;
  return typeof bridge.windowId === "string" && bridge.windowId
    ? bridge.windowId
    : "main";
}

export function projectionWindowId(windowId?: string | null): string {
  return windowId || desktopWindowId() || "main";
}

export function registerPendingTransfer(
  entry: TransferJournalEntry,
  windowId?: string | null,
): void {
  const id = projectionWindowId(windowId);
  const entries = entriesByWindow.get(id) ?? new Map<string, TransferJournalEntry>();
  entries.set(entry.token, entry);
  entriesByWindow.set(id, entries);
}

export function pendingTransfer(
  token: string,
  windowId?: string | null,
): TransferJournalEntry | undefined {
  return entriesByWindow.get(projectionWindowId(windowId))?.get(token);
}

export function pendingTransfers(
  windowId?: string | null,
): TransferJournalEntry[] {
  return [...(entriesByWindow.get(projectionWindowId(windowId))?.values() ?? [])];
}

export function unregisterPendingTransfer(
  token: string,
  windowId?: string | null,
): TransferJournalEntry | undefined {
  const id = projectionWindowId(windowId);
  const entries = entriesByWindow.get(id);
  const entry = entries?.get(token);
  if (!entries) return entry;
  entries.delete(token);
  if (entries.size === 0) entriesByWindow.delete(id);
  return entry;
}
