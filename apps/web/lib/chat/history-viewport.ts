export interface HistoryAnchor { id: string; offset: number; head?: string | null }
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
