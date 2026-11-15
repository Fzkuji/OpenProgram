import { create } from 'zustand';

/** The read protocol this client speaks: whole turns and a numbered window. */
export const HISTORY_PROTOCOL = 3;

export interface HistoryPage {
  head_id: string | null; before: string | null; after?: string | null;
  revision?: number; start?: number; end?: number; total?: number;
}

/** Whether this view came from a read: rows without a window are leftovers. */
export function holdsHistoryWindow(page: HistoryPage | undefined): boolean {
  return page?.revision !== undefined;
}
export interface HistoryState extends HistoryPage { generation: number; loading: boolean; error: boolean }
let generation = 0;
export const useSessionHistory = create<{ pages: Record<string, HistoryState> }>(() => ({ pages: {} }));
export function registerSessionHistory(id: string, history?: HistoryPage) {
  useSessionHistory.setState(s => ({ pages: { ...s.pages, [id]: {
    ...history,
    head_id: history?.head_id ?? null, before: history?.before ?? null,
    generation: ++generation, loading: false, error: false,
  } } }));
}
export function updateSessionHistory(id: string, expected: number, patch: Partial<HistoryState>): boolean {
  let accepted = false;
  useSessionHistory.setState(s => {
    const current = s.pages[id];
    if (!current || current.generation !== expected) return s;
    accepted = true;
    return { pages: { ...s.pages, [id]: { ...current, ...patch } } };
  });
  return accepted;
}
