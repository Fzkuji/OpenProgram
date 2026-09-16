export interface HistoryAnchor { id: string; offset: number; head?: string | null }

const followLocks = new Set<string>();

export function setFollowLock(scrollerKey: string, locked: boolean): void {
  if (!scrollerKey) return;
  if (locked) followLocks.add(scrollerKey);
  else followLocks.delete(scrollerKey);
}

export function isFollowLocked(scrollerKey: string): boolean {
  return followLocks.has(scrollerKey);
}
const STORAGE_KEY = 'chatReadingAnchors';

export function captureHistoryAnchor(area: HTMLElement): HistoryAnchor | null {
  const top = area.getBoundingClientRect().top;
  const rows = area.querySelectorAll<HTMLElement>('[data-msg-slot], [data-msg-id]');
  for (const row of rows) {
    const rect = row.getBoundingClientRect();
    if (rect.height > 0 && rect.bottom > top) {
      const id = row.dataset.msgSlot || row.dataset.msgId;
      if (id) return { id, offset: rect.top - top };
    }
  }
  return null;
}
export function restoreHistoryAnchor(area: HTMLElement, anchor: HistoryAnchor): boolean {
  const row = Array.from(area.querySelectorAll<HTMLElement>('[data-msg-slot], [data-msg-id]'))
    .find(el => (el.dataset.msgSlot || el.dataset.msgId) === anchor.id);
  if (!row) return false;
  area.scrollTop += row.getBoundingClientRect().top - area.getBoundingClientRect().top - anchor.offset;
  area.dispatchEvent(new Event('scroll'));
  return true;
}
export function readHistoryAnchor(id: string): HistoryAnchor | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}')[id];
    return value && typeof value.id === 'string' && Number.isFinite(value.offset) ? value : null;
  } catch { return null; }
}
export function saveHistoryAnchor(id: string, anchor: HistoryAnchor | null): void {
  try {
    const map = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}');
    delete map[id];
    if (anchor) map[id] = anchor;
    const entries = Object.entries(map).slice(-64);
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch { /* Storage may be disabled. */ }
}

export interface AreaRestoreState {
  area: HTMLElement;
  chatKey: string;
  anchor: HistoryAnchor | null;
  oldTop: number;
  oldHeight: number;
  /** True when this area was in the session viewport registry at capture. */
  fromRegistry: boolean;
}

export function captureAreaRestoreState(
  area: HTMLElement,
  chatKey: string,
  fromRegistry = false,
): AreaRestoreState {
  return {
    area,
    chatKey,
    anchor: captureHistoryAnchor(area),
    oldTop: area.scrollTop,
    oldHeight: area.scrollHeight,
    fromRegistry,
  };
}

function isCurrentChatArea(area: HTMLElement): boolean {
  return typeof document !== "undefined"
    && typeof document.getElementById === "function"
    && document.getElementById("chatArea") === area;
}

/** Restore only when this area still shows the captured session and chatKey. */
export function historyAreaStillOwned(
  cap: Pick<AreaRestoreState, "area" | "chatKey" | "fromRegistry">,
  liveRegistered: Map<HTMLElement, string> | undefined,
  owner: {
    sessionId: string;
    focusedSessionId: string | null;
    focusedChatKey: string | null;
  },
): boolean {
  const registeredKey = liveRegistered?.get(cap.area);
  if (registeredKey != null) return registeredKey === cap.chatKey;
  if (cap.fromRegistry) return false;
  if (!isCurrentChatArea(cap.area) || !cap.area.isConnected) return false;
  if (owner.focusedSessionId !== owner.sessionId) return false;
  return (owner.focusedChatKey ?? owner.sessionId) === cap.chatKey;
}

export function restoreAreaWindow(
  state: AreaRestoreState,
  direction: string,
  around?: string,
): void {
  const { area, anchor, oldTop, oldHeight, chatKey } = state;
  if (!area.isConnected) return;
  if (isFollowLocked(chatKey)) return;
  if (direction === "around" && around) {
    restoreHistoryAnchor(area, { id: around, offset: 0 });
  } else if (!anchor || !restoreHistoryAnchor(area, anchor)) {
    area.scrollTop = oldTop + area.scrollHeight - oldHeight;
    area.dispatchEvent(new Event("scroll"));
  }
}
