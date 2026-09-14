import type { HistoryPage } from './session-history';

export const HISTORY_WINDOW_PAGES = 6;
export const HISTORY_WINDOW_BYTES = 8 * 1024 * 1024;
export type HistoryDirection = 'older' | 'newer' | 'around' | 'latest';
export type HistoryRow = { id?: unknown; [key: string]: unknown };
interface Chunk { history: HistoryPage; messages: HistoryRow[]; bytes: number }

/** Contiguous pages; eviction never splits a turn from its caller descendants. */
export class HistoryWindow {
  private chunks: Chunk[] = [];
  get messages(): HistoryRow[] { return this.chunks.flatMap(p => p.messages); }
  get pageCount(): number { return this.chunks.length; }
  get bytes(): number { return this.chunks.reduce((n, p) => n + p.bytes, 0); }
  get history(): HistoryPage | undefined {
    const first = this.chunks[0], last = this.chunks.at(-1);
    return first && last ? { ...last.history, start: first.history.start, before: first.history.before } : undefined;
  }
  add(messages: HistoryRow[], history: HistoryPage, direction: HistoryDirection, anchor?: string): void {
    if (direction === 'around' || direction === 'latest' || this.history?.snapshot !== history.snapshot) this.chunks = [];
    const chunk = { messages, history, bytes: new TextEncoder().encode(JSON.stringify(messages)).length };
    this.chunks = this.chunks.filter(p => p.history.start !== history.start);
    this.chunks.push(chunk);
    this.chunks.sort((a,b) => (a.history.start ?? 0) - (b.history.start ?? 0));
    while (this.chunks.length > 1 && (this.pageCount > HISTORY_WINDOW_PAGES || this.bytes > HISTORY_WINDOW_BYTES)) {
      const front = direction === 'newer';
      const index = front ? 0 : this.chunks.length - 1;
      const containsAnchor = (p: Chunk) => !!anchor && p.messages.some(m => m.id === anchor);
      if (containsAnchor(this.chunks[index])) {
        this.chunks.splice(front ? this.chunks.length - 1 : 0, 1);
      } else this.chunks.splice(index, 1);
    }
  }
}
