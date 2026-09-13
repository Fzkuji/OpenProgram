import { create } from 'zustand';

export interface HistoryPage { head_id: string | null; before: string | null }
export interface HistoryState extends HistoryPage { generation: number; loading: boolean; error: boolean }
let generation = 0;
export const useSessionHistory = create<{ pages: Record<string, HistoryState> }>(() => ({ pages: {} }));
export function registerSessionHistory(id: string, history?: HistoryPage) {
  useSessionHistory.setState(s => ({ pages: { ...s.pages, [id]: {
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
