import type { HistoryPage } from './session-history.ts';

export const HISTORY_WINDOW_PAGES = 6;
export const HISTORY_WINDOW_BYTES = 8 * 1024 * 1024;
export type HistoryDirection = 'older' | 'newer' | 'around' | 'latest';
export type HistoryRow = { id?: unknown; [key: string]: unknown };
export type WireTurn = { id: string; rows: HistoryRow[] };
export type TurnWindow = HistoryPage & { revision: number; start: number; end: number; total: number };
interface Chunk { history: HistoryPage; messages: HistoryRow[]; bytes: number; turnIds?: string[] }

/** Contiguous pages of whole turns, so eviction never splits a turn. A chunk
 *  holds row ids; the bodies live in the session store. */
export class HistoryWindow {
  private chunks: Chunk[] = [];
  get messages(): HistoryRow[] { return this.chunks.flatMap(p => p.messages); }
  get rowIds(): string[] { return this.messages.map(m => m.id as string); }
  get pageCount(): number { return this.chunks.length; }
  get bytes(): number { return this.chunks.reduce((n, p) => n + p.bytes, 0); }
  get history(): HistoryPage | undefined {
    const first = this.chunks[0], last = this.chunks.at(-1);
    return first && last ? { ...last.history, start: first.history.start, before: first.history.before } : undefined;
  }
  /** Place a page of turns by its position. A page that neither touches nor
   *  overlaps what is held is dropped, and this returns false. A page reply
   *  never moves the window's revision. */
  addTurns(turns: WireTurn[], window: TurnWindow, direction: HistoryDirection, anchor?: string): boolean {
    const held = this.history;
    const replaces = direction === 'around' || direction === 'latest' || !held;
    if (!replaces && (window.end < (held.start ?? 0) || window.start > (held.end ?? 0))) return false;
    if (replaces) this.chunks = [];
    const taken = new Set(this.chunks.flatMap(p => p.turnIds ?? []));
    const fresh = turns.map((turn, at) => ({ turn, at })).filter(({ turn }) => !taken.has(turn.id));
    if (fresh.length) {
      const start = window.start + fresh[0].at, end = window.start + fresh[fresh.length - 1].at + 1;
      const history: HistoryPage = {
        ...window, start, end,
        revision: replaces ? window.revision : held?.revision,
        before: start > 0 ? fresh[0].turn.id : null,
        after: end < window.total ? fresh[fresh.length - 1].turn.id : null,
      };
      const rows = fresh.flatMap(({ turn }) => turn.rows);
      this.chunks.push({
        history, messages: rows.map(row => ({ id: row.id })), turnIds: fresh.map(({ turn }) => turn.id),
        bytes: new TextEncoder().encode(JSON.stringify(rows)).length,
      });
    }
    this.evict(direction, anchor);
    return true;
  }

  holdsTurn(turnId: string): boolean {
    return this.chunks.some((chunk) => chunk.turnIds?.includes(turnId));
  }

  /** New rows drawn inside held turns, placed in the window after their row. */
  noteRows(added: { turnId: string; afterRowId: string | null; rowId: string }[]): void {
    for (const { afterRowId, rowId } of added) {
      const chunk = this.chunks.find((p) => afterRowId !== null && p.messages.some((m) => m.id === afterRowId)) ?? this.chunks.at(-1);
      if (!chunk) continue;
      const after = afterRowId === null ? -1 : chunk.messages.findIndex((m) => m.id === afterRowId);
      chunk.messages.splice(after >= 0 ? after + 1 : chunk.messages.length, 0, { id: rowId });
    }
  }

  /** New turns past the newest held, from numbered updates. */
  noteTurns(turns: WireTurn[], turnsPastWindow = 0): void {
    const last = this.chunks.at(-1);
    if (!last) return;
    for (const turn of turns) {
      last.turnIds = [...(last.turnIds ?? []), turn.id];
      last.messages = [...last.messages, ...turn.rows.map((row) => ({ id: row.id }))];
      last.bytes += new TextEncoder().encode(JSON.stringify(turn.rows)).length;
    }
    const added = turns.length + turnsPastWindow;
    if (added) last.history = { ...last.history, end: (last.history.end ?? 0) + turns.length, total: (last.history.total ?? 0) + added };
  }

  private evict(direction: HistoryDirection, anchor?: string): void {
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
